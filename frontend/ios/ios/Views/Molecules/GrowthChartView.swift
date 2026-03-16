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
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    
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

    // IQR-based range for YoY normalization — prevents outliers from flattening the line
    private var yoyDisplayRange: (min: Double, max: Double) {
        let sorted = allPercentValues.sorted()
        guard sorted.count >= 4 else {
            let lo = sorted.first ?? -10
            let hi = sorted.last ?? 10
            let padding = max((hi - lo) * 0.2, 10)
            return (lo - padding, hi + padding)
        }
        let q1 = sorted[sorted.count / 4]
        let q3 = sorted[3 * sorted.count / 4]
        let iqr = q3 - q1
        let fence = max(iqr * 1.5, 5)
        let rangeMin = q1 - fence
        let rangeMax = q3 + fence
        let padding = max((rangeMax - rangeMin) * 0.1, 5)
        return (rangeMin - padding, rangeMax + padding)
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
        HStack(alignment: .top, spacing: 0) {
            // Left column: Y-axis labels (fixed, never scrolls)
            VStack(spacing: 0) {
                barYAxisLabels

                // Spacer matching x-axis labels + YoY labels rows
                Spacer()
                    .frame(height: 20 + AppSpacing.md + 20 + AppSpacing.sm)
            }
            .frame(width: yAxisWidth)

            // Right column: scrollable chart area
            GeometryReader { geometry in
                let visibleWidth = geometry.size.width
                let needsScroll = dataPoints.count > Int(visibleColumnCount)
                let contentWidth = needsScroll
                    ? CGFloat(dataPoints.count) * (visibleWidth / visibleColumnCount)
                    : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(spacing: 0) {
                        chartArea
                            .frame(height: chartHeight)

                        xAxisLabels
                            .padding(.top, AppSpacing.md)

                        yoyPercentageLabels
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: contentWidth)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + 20 + AppSpacing.md + 20 + AppSpacing.sm + (dataPoints.count > Int(visibleColumnCount) ? AppSpacing.md : 0))
        }
    }

    // MARK: - Chart Area

    private var chartArea: some View {
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

        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: minBarValue...maxBarValue)
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
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
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                if shouldShowLabel(for: dataPoint) {
                    Text(dataPoint.period)
                        .font(.system(size: labelFontSize, weight: .regular))
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .fixedSize()
                        .position(
                            x: columnWidth * CGFloat(index) + columnWidth / 2,
                            y: 10
                        )
                }
            }
        }
        .frame(height: 20)
    }

    // MARK: - YoY Percentage Labels

    private var yoyPercentageLabels: some View {
        GeometryReader { geometry in
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                if shouldShowLabel(for: dataPoint) {
                    Text(String(format: "%.1f%%", dataPoint.yoyChangePercent))
                        .font(.system(size: yoyFontSize, weight: .semibold))
                        .foregroundColor(dataPoint.yoyChangePercent >= 0 ? AppColors.bullish : AppColors.bearish)
                        .lineLimit(1)
                        .fixedSize()
                        .position(
                            x: columnWidth * CGFloat(index) + columnWidth / 2,
                            y: 10
                        )
                }
            }
        }
        .frame(height: 20)
    }
    
    // MARK: - Helper Functions - Label Display
    
    private func shouldShowLabel(for dataPoint: GrowthDataPoint) -> Bool {
        // With horizontal scrolling, each column has ~50pt of space,
        // so all labels are readable. Show every label.
        return true
    }

    // MARK: - Helper Functions

    /// Normalize YoY percentage to fit within the bar chart's value range
    /// Uses IQR-based range so outliers don't flatten the line
    private func normalizeYoY(_ yoyPercent: Double) -> Double {
        let range = yoyDisplayRange
        let span = range.max - range.min
        guard span > 0 else { return maxBarValue * 0.5 }
        let normalized = (yoyPercent - range.min) / span
        let clamped = min(max(normalized, 0.0), 1.0)
        let targetMin = maxBarValue * 0.10
        let targetMax = maxBarValue * 0.85
        return targetMin + clamped * (targetMax - targetMin)
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
