//
//  ProfitPowerChartView.swift
//  ios
//
//  Molecule: Multi-line chart displaying profit margin metrics over time
//  Uses native Swift Charts framework
//

import SwiftUI
import Charts

struct ProfitPowerChartView: View {
    let dataPoints: [ProfitPowerDataPoint]
    @Binding var selectedDataPoint: ProfitPowerDataPoint?

    // Chart configuration
    private let chartHeight: CGFloat = 240
    private let yAxisWidth: CGFloat = 40

    // Computed properties for chart bounds
    private var maxMargin: Double {
        let allValues = dataPoints.flatMap { [
            $0.grossMargin, $0.operatingMargin, $0.fcfMargin,
            $0.netMargin, $0.sectorAverageNetMargin
        ] }
        // Round up to nearest 10 for cleaner axis
        let maxValue = allValues.max() ?? 50
        return ceil(maxValue / 10) * 10
    }

    private var minMargin: Double {
        0 // Start from 0 for margin charts
    }

    // Grid line values (5 horizontal lines at 0%, 10%, 20%, 30%, 40%, 50%)
    private var gridValues: [Double] {
        let step = maxMargin / 5
        return stride(from: step, to: maxMargin, by: step).map { $0 }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Chart with all margin lines
            chartContent

            // X-axis labels (periods)
            xAxisLabels
        }
        // Overlay tooltip when a data point is selected
        .overlay(alignment: .top) {
            if let selectedDataPoint {
                ProfitPowerTooltipView(dataPoint: selectedDataPoint)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.top, AppSpacing.xs)
                    .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.7), value: selectedDataPoint?.id)
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        HStack(alignment: .top, spacing: 0) {
            // Y-axis labels for percentages
            yAxisLabels
                .frame(width: yAxisWidth)

            // Main chart area
            Chart {
                // Horizontal grid lines
                ForEach(gridValues, id: \.self) { value in
                    RuleMark(y: .value("Grid", value))
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.6))
                        .lineStyle(StrokeStyle(lineWidth: 0.5))
                }

                // Gross Margin Line (Blue - highest)
                marginLineMark(for: .grossMargin)
                marginPointMark(for: .grossMargin)

                // Net Margin Line (Green)
                marginLineMark(for: .netMargin)
                marginPointMark(for: .netMargin)

                // Sector Average Line (Gray - dashed)
                sectorAverageLineMark
                sectorAveragePointMark

                // Operating Margin Line (Orange)
                marginLineMark(for: .operatingMargin)
                marginPointMark(for: .operatingMargin)

                // FCF Margin Line (Purple)
                marginLineMark(for: .fcfMargin)
                marginPointMark(for: .fcfMargin)
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartYScale(domain: minMargin...maxMargin)
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        updateSelection(at: value.location)
                    }
                    .onEnded { _ in
                        // Keep selection visible for a moment, then hide
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                            selectedDataPoint = nil
                        }
                    }
            )
        }
    }

    // MARK: - Line Marks

    @ChartContentBuilder
    private func marginLineMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(dataPoints) { dataPoint in
            LineMark(
                x: .value("Period", dataPoint.period),
                y: .value("Margin", dataPoint.margin(for: type)),
                series: .value("Series", type.rawValue)
            )
            .foregroundStyle(type.color)
            .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
            .interpolationMethod(.catmullRom)
        }
    }

    @ChartContentBuilder
    private func marginPointMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(dataPoints) { dataPoint in
            PointMark(
                x: .value("Period", dataPoint.period),
                y: .value("Margin", dataPoint.margin(for: type))
            )
            .foregroundStyle(type.color)
            .symbolSize(40)
        }
    }

    @ChartContentBuilder
    private var sectorAverageLineMark: some ChartContent {
        ForEach(dataPoints) { dataPoint in
            LineMark(
                x: .value("Period", dataPoint.period),
                y: .value("Sector", dataPoint.sectorAverageNetMargin),
                series: .value("Series", "SectorAverage")
            )
            .foregroundStyle(AppColors.profitSectorAverage)
            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 4]))
            .interpolationMethod(.catmullRom)
        }
    }

    @ChartContentBuilder
    private var sectorAveragePointMark: some ChartContent {
        ForEach(dataPoints) { dataPoint in
            PointMark(
                x: .value("Period", dataPoint.period),
                y: .value("Sector", dataPoint.sectorAverageNetMargin)
            )
            .foregroundStyle(AppColors.profitSectorAverage)
            .symbolSize(30)
        }
    }

    // MARK: - Y-Axis Labels

    private var yAxisLabels: some View {
        VStack {
            Text(String(format: "%.0f%%", maxMargin))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.8))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.6))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.4))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.2))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text("0%")
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

    // MARK: - Selection Helper

    private func updateSelection(at location: CGPoint) {
        // Calculate which data point is closest to the tap location
        // Account for the y-axis width offset
        let adjustedX = location.x - yAxisWidth
        let chartWidth = UIScreen.main.bounds.width - (yAxisWidth + AppSpacing.lg * 2)
        let pointWidth = chartWidth / CGFloat(dataPoints.count)

        let index = Int(adjustedX / pointWidth)
        if index >= 0 && index < dataPoints.count {
            selectedDataPoint = dataPoints[index]
        }
    }
}

// MARK: - Profit Power Tooltip View

struct ProfitPowerTooltipView: View {
    let dataPoint: ProfitPowerDataPoint

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Period header
            Text(dataPoint.period)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
                .padding(.bottom, AppSpacing.xxs)

            // All margin values
            tooltipRow(
                title: "Gross Margin",
                value: dataPoint.grossMargin,
                color: AppColors.profitGrossMargin
            )

            tooltipRow(
                title: "Operating Margin",
                value: dataPoint.operatingMargin,
                color: AppColors.profitOperatingMargin
            )

            tooltipRow(
                title: "FCF Margin",
                value: dataPoint.fcfMargin,
                color: AppColors.profitFCFMargin
            )

            tooltipRow(
                title: "Net Margin",
                value: dataPoint.netMargin,
                color: AppColors.profitNetMargin
            )

            tooltipRow(
                title: "Sector Avg",
                value: dataPoint.sectorAverageNetMargin,
                color: AppColors.profitSectorAverage
            )
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
                .shadow(color: Color.black.opacity(0.15), radius: 8, x: 0, y: 4)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }

    private func tooltipRow(title: String, value: Double, color: Color) -> some View {
        HStack(spacing: AppSpacing.sm) {
            // Color indicator
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            // Title
            Text(title)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            // Value
            Text(String(format: "%.1f%%", value))
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            ProfitPowerChartView(
                dataPoints: ProfitPowerSectionData.sampleData.annualData,
                selectedDataPoint: .constant(nil)
            )
            .padding()
        }
    }
}
