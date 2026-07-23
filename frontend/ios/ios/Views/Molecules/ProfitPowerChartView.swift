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
    /// "Industry" / "Sector" for the benchmark line's tooltip label, matching
    /// the legend. Defaults to "Sector" for callers that don't plumb it.
    var peerWord: String = "Sector"
    /// Pending tooltip auto-dismiss, so a new tap replaces the old timer.
    @State private var tooltipDismissTask: DispatchWorkItem?

    // Chart configuration
    private let chartHeight: CGFloat = 240
    private let yAxisWidth: CGFloat = 40
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    private let xAxisHeight: CGFloat = 20

    /// Every non-nil margin on screen. `compactMap` — a nil margin is absent
    /// data (a bank has no gross profit), not a 0.
    private var allValues: [Double] {
        dataPoints.flatMap {
            [$0.grossMargin, $0.operatingMargin, $0.fcfMargin,
             $0.netMargin, $0.sectorAverageNetMargin].compactMap { $0 }
        }
    }

    /// True when there is nothing to plot — the caller shows an empty state
    /// instead of a fabricated axis.
    private var hasData: Bool { !allValues.isEmpty }

    /// Chart bounds. Rounded to a multiple of 10 for a clean axis, then run
    /// through ChartDomain so an all-zero or all-negative series can't produce
    /// a zero-width (crash) or inverted domain.
    private var marginDomain: ClosedRange<Double> {
        let rounded = allValues.map { ceil($0 / 10) * 10 } + allValues.map { floor($0 / 10) * 10 }
        return ChartDomain.make(
            rounded, includeZero: true, headroomFraction: 0.0, fallback: 0...50
        )
    }

    private var maxMargin: Double { marginDomain.upperBound }
    private var minMargin: Double { marginDomain.lowerBound }

    // Grid line values (5 horizontal lines). Was
    // `stride(from:to:by: (max-min)/5)`, which is a HARD CRASH when every
    // margin is 0 (`stride` traps on a zero step).
    private var gridValues: [Double] {
        ChartDomain.gridValues(in: marginDomain, count: 4)
    }

    // Consistent sizes for both Annual and Quarterly
    private let symbolSize: CGFloat = 40
    private let lineWidth: CGFloat = 2.5

    private var needsScroll: Bool {
        dataPoints.count > Int(visibleColumnCount)
    }

    var body: some View {
        if hasData {
            chartBody
        } else {
            // No plottable margin anywhere in the series. Drawing the chart
            // here rendered an invented 0–50% axis with no lines on it, which
            // reads as "margins are zero" rather than "we have no data".
            ChartUnavailableView(message: "Margin data isn't available for this company.")
                .frame(height: chartHeight + xAxisHeight + AppSpacing.sm)
        }
    }

    private var chartBody: some View {
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
                ProfitPowerTooltipView(dataPoint: selectedDataPoint, peerWord: peerWord)
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
        .chartYScale(domain: marginDomain)
        // The categorical band scale carries Swift Charts' default plot inset,
        // while the manual xAxisLabels row below divides the same width flush —
        // without this the edge labels sat beside their marks. Matches
        // GrowthChartView / ProfitabilityChartView.
        .chartXScale(range: .plotDimension(padding: edgeLabelPad(for: contentWidth)))
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
        .contentShape(Rectangle())
        .onTapGesture { location in
            updateSelection(at: location, chartWidth: contentWidth)
            // Cancel any previously scheduled dismissal: every tap used to
            // schedule an unconditional clear, so tapping B shortly after A
            // dismissed B early (and rapid taps left N pending closures).
            tooltipDismissTask?.cancel()
            let task = DispatchWorkItem { selectedDataPoint = nil }
            tooltipDismissTask = task
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5, execute: task)
        }
    }

    /// Half a column — the inset that lines the categorical band centres up
    /// with the flush-divided label row.
    private func edgeLabelPad(for contentWidth: CGFloat) -> CGFloat {
        guard dataPoints.count > 0, contentWidth.isFinite, contentWidth > 0 else { return 0 }
        return contentWidth / CGFloat(dataPoints.count) / 2
    }

    // MARK: - Line Marks

    // Each mark builder skips points whose margin is nil, so an absent value
    // renders as a GAP in the line instead of a fabricated 0% reading.

    @ChartContentBuilder
    private func marginLineMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(dataPoints) { dataPoint in
            if let value = dataPoint.margin(for: type) {
                LineMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Margin", value),
                    series: .value("Series", type.rawValue)
                )
                .foregroundStyle(type.color)
                .lineStyle(StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
            }
        }
    }

    @ChartContentBuilder
    private func marginPointMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(dataPoints) { dataPoint in
            if let value = dataPoint.margin(for: type) {
                PointMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Margin", value)
                )
                .foregroundStyle(type.color)
                .symbolSize(symbolSize)
            }
        }
    }

    @ChartContentBuilder
    private var sectorAverageLineMark: some ChartContent {
        ForEach(dataPoints) { dataPoint in
            if let value = dataPoint.sectorAverageNetMargin {
                LineMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Sector", value),
                    series: .value("Series", "SectorAverage")
                )
                .foregroundStyle(AppColors.profitSectorAverage)
                .lineStyle(StrokeStyle(lineWidth: lineWidth - 0.5, lineCap: .round, lineJoin: .round, dash: [6, 4]))
            }
        }
    }

    @ChartContentBuilder
    private var sectorAveragePointMark: some ChartContent {
        ForEach(dataPoints) { dataPoint in
            if let value = dataPoint.sectorAverageNetMargin {
                PointMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Sector", value)
                )
                .foregroundStyle(AppColors.profitSectorAverage)
                .symbolSize(symbolSize * 0.75)
            }
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
        // The bounds check used to happen AFTER `Int(location.x / pointWidth)`.
        // A GeometryReader reporting width 0 mid-transition makes that quotient
        // infinite (or NaN with an empty series), and `Int(.infinity)` TRAPS.
        // ChartDomain.columnIndex validates before converting.
        guard let index = ChartDomain.columnIndex(
            atX: location.x, width: chartWidth, count: dataPoints.count
        ) else { return }
        selectedDataPoint = dataPoints[index]
    }
}

// MARK: - Profit Power Tooltip View

struct ProfitPowerTooltipView: View {
    let dataPoint: ProfitPowerDataPoint
    var peerWord: String = "Sector"

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
                title: "\(peerWord) Avg",
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

    /// `value == nil` means the margin genuinely isn't reported for this period
    /// (a bank has no gross profit; a thin industry has no sector median). Show
    /// an em dash rather than "0.0%", which reads as a real measurement.
    private func tooltipRow(title: String, value: Double?, color: Color) -> some View {
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
            Text(value.map { String(format: "%.1f%%", $0) } ?? "—")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(value == nil ? AppColors.textMuted : AppColors.textPrimary)
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
