//
//  ProfitPowerChartView.swift
//  ios
//
//  Molecule: Multi-line chart displaying profit margin metrics over time
//  Uses native Swift Charts framework with horizontal scrolling for deep history
//

import SwiftUI
import Charts

struct ProfitPowerChartView: View {
    let dataPoints: [ProfitPowerDataPoint]
    @Binding var selectedDataPoint: ProfitPowerDataPoint?

    // Chart configuration
    private let chartHeight: CGFloat = 240
    private let yAxisWidth: CGFloat = 40
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    private let xAxisHeight: CGFloat = 20

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
        let allValues = dataPoints.flatMap { [
            $0.grossMargin, $0.operatingMargin, $0.fcfMargin,
            $0.netMargin, $0.sectorAverageNetMargin
        ] }
        let minValue = allValues.min() ?? 0
        return minValue < 0 ? floor(minValue / 10) * 10 : 0
    }

    // Grid line values (5 horizontal lines)
    private var gridValues: [Double] {
        let range = maxMargin - minMargin
        let step = range / 5
        return stride(from: minMargin + step, to: maxMargin, by: step).map { $0 }
    }

    // Adaptive sizes for dense data
    private var symbolSize: CGFloat {
        dataPoints.count > 20 ? 15 : 40
    }

    private var lineWidth: CGFloat {
        dataPoints.count > 20 ? 1.5 : 2.5
    }

    private var needsScroll: Bool {
        dataPoints.count > Int(visibleColumnCount)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: fixed Y-axis labels (never scrolls)
            VStack(spacing: 0) {
                yAxisLabels

                // Spacer matching x-axis labels height
                Spacer()
                    .frame(height: xAxisHeight + AppSpacing.sm)
            }
            .frame(width: yAxisWidth)

            // Right column: scrollable chart area
            GeometryReader { geometry in
                let visibleWidth = geometry.size.width
                let contentWidth = needsScroll
                    ? CGFloat(dataPoints.count) * (visibleWidth / visibleColumnCount)
                    : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(spacing: 0) {
                        chartArea(contentWidth: contentWidth)
                            .frame(height: chartHeight)

                        xAxisLabels
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: contentWidth)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + xAxisHeight + AppSpacing.sm + (needsScroll ? AppSpacing.md : 0))
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

    // MARK: - Chart Area

    private func chartArea(contentWidth: CGFloat) -> some View {
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
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { value in
                    updateSelection(at: value.location, chartWidth: contentWidth)
                }
                .onEnded { _ in
                    // Keep selection visible for a moment, then hide
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                        selectedDataPoint = nil
                    }
                }
        )
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
            .lineStyle(StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
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
            .symbolSize(symbolSize)
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
            .lineStyle(StrokeStyle(lineWidth: lineWidth - 0.5, lineCap: .round, lineJoin: .round, dash: [6, 4]))
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
            .symbolSize(symbolSize * 0.75)
        }
    }

    // MARK: - Y-Axis Labels

    private var yAxisLabels: some View {
        VStack {
            Text(String(format: "%.0f%%", maxMargin))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.8 + minMargin * 0.2))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.6 + minMargin * 0.4))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.4 + minMargin * 0.6))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", maxMargin * 0.2 + minMargin * 0.8))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(String(format: "%.0f%%", minMargin))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - X-Axis Labels

    private var xAxisLabels: some View {
        HStack(spacing: 0) {
            ForEach(dataPoints) { dataPoint in
                Text(dataPoint.period)
                    .font(.system(size: 11))
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .frame(height: xAxisHeight)
    }

    // MARK: - Selection Helper

    private func updateSelection(at location: CGPoint, chartWidth: CGFloat) {
        let pointWidth = chartWidth / CGFloat(dataPoints.count)
        let index = Int(location.x / pointWidth)
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
                .font(AppTypography.bodySmallEmphasis)
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
                .font(AppTypography.captionEmphasis)
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
