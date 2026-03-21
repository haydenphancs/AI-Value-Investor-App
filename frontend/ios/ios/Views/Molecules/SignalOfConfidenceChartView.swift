//
//  SignalOfConfidenceChartView.swift
//  ios
//
//  Molecule: Combined bar and line chart for Signal of Confidence using Swift Charts
//  Displays dividends (bars), buybacks (bars), and shares outstanding (line)
//  Supports horizontal scrolling when data points exceed visible column count
//

import SwiftUI
import Charts

struct SignalOfConfidenceChartView: View {
    let dataPoints: [SignalOfConfidenceDataPoint]
    let viewType: SignalOfConfidenceViewType

    // Chart configuration
    private let chartHeight: CGFloat = 280
    private let yAxisWidth: CGFloat = 30
    private let rightYAxisWidth: CGFloat = 38
    private let visibleColumnCount: CGFloat = 6
    private let labelRowHeight: CGFloat = 20
    private let labelRowCount: CGFloat = 3 // dividends, buybacks, shares outstanding

    // MARK: - Computed Properties

    private var maxBarValue: Double {
        switch viewType {
        case .yield:
            let maxTotal = dataPoints.map { $0.dividendYield + $0.buybackYield }.max() ?? 1
            return maxTotal * 1.15
        case .capital:
            let maxTotal = dataPoints.map { $0.dividendAmount + $0.buybackAmount }.max() ?? 1
            return maxTotal * 1.15
        }
    }

    private var sharesRange: (min: Double, max: Double) {
        let shares = dataPoints.map { $0.sharesOutstanding }
        let minShares = (shares.min() ?? 0) * 0.9
        let maxShares = (shares.max() ?? 1) * 1.1
        return (minShares, maxShares)
    }

    // Grid line values (4 horizontal lines)
    private var gridValues: [Double] {
        let step = maxBarValue / 4
        return [step, step * 2, step * 3]
    }

    /// Total height of x-axis + data label rows below the chart
    private var belowChartHeight: CGFloat {
        // x-axis row + padding + (label row + padding) * 3
        labelRowHeight + AppSpacing.sm + (labelRowHeight + AppSpacing.sm) * labelRowCount
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left Y-axis labels (fixed, never scrolls)
            VStack(spacing: 0) {
                leftYAxisLabels

                // Spacer matching below-chart rows
                Spacer()
                    .frame(height: belowChartHeight)
            }
            .frame(width: yAxisWidth)

            // Scrollable chart + x-axis + data labels area
            GeometryReader { geometry in
                let visibleWidth = geometry.size.width
                let needsScroll = dataPoints.count > Int(visibleColumnCount)
                let contentWidth = needsScroll
                    ? CGFloat(dataPoints.count) * (visibleWidth / visibleColumnCount)
                    : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(spacing: 0) {
                        chartContent
                            .frame(height: chartHeight)
                            .id(viewType)

                        scrollableXAxisLabels
                            .padding(.top, AppSpacing.sm)

                        dividendLabels
                            .padding(.top, AppSpacing.sm)

                        buybackLabels
                            .padding(.top, AppSpacing.sm)

                        sharesOutstandingLabels
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: contentWidth)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + belowChartHeight + (dataPoints.count > Int(visibleColumnCount) ? AppSpacing.md : 0))

            // Right Y-axis labels (fixed, never scrolls)
            VStack(spacing: 0) {
                rightYAxisLabels

                // Spacer matching below-chart rows
                Spacer()
                    .frame(height: belowChartHeight)
            }
            .frame(width: rightYAxisWidth)
        }
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        Chart {
            // Horizontal grid lines
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // Dividend bars
            ForEach(dataPoints) { dataPoint in
                BarMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Dividends", viewType == .yield ? dataPoint.dividendYield : dataPoint.dividendAmount),
                    width: dataPoints.count > 6 ? .fixed(18) : .fixed(18)
                )
                .foregroundStyle(AppColors.confidenceDividends)
                .cornerRadius(3)
                .position(by: .value("Type", "Dividends"))
            }

            // Buyback bars
            ForEach(dataPoints) { dataPoint in
                BarMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Buybacks", viewType == .yield ? dataPoint.buybackYield : dataPoint.buybackAmount),
                    width: dataPoints.count > 6 ? .fixed(18) : .fixed(18)
                )
                .foregroundStyle(AppColors.confidenceBuybacks)
                .cornerRadius(3)
                .position(by: .value("Type", "Buybacks"))
            }

            // Shares outstanding line
            ForEach(dataPoints) { dataPoint in
                LineMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.linear)
            }

            // Shares outstanding points
            ForEach(dataPoints) { dataPoint in
                PointMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .symbolSize(50)
            }

            // Dashed connector line from newest shares outstanding to right Y-axis
            if let lastShares = dataPoints.last?.sharesOutstanding {
                RuleMark(y: .value("SharesConnector", normalizeShares(lastShares)))
                    .foregroundStyle(AppColors.confidenceSharesOutstanding.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: 0...maxBarValue)
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
    }

    // MARK: - Y-Axis Labels

    private var leftYAxisLabels: some View {
        VStack {
            Text(formatLeftAxisValue(maxBarValue * 0.9))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLeftAxisValue(maxBarValue * 0.6))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLeftAxisValue(maxBarValue * 0.3))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(viewType == .yield ? "0%" : "$0")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    /// Newest shares outstanding value for right Y-axis highlight
    private var newestShares: Double {
        dataPoints.last?.sharesOutstanding ?? 0
    }

    /// Vertical position (0 = top, 1 = bottom) of the newest shares on the right Y-axis
    private var newestSharesYPosition: CGFloat {
        let range = sharesRange.max - sharesRange.min
        guard range > 0 else { return 0.5 }
        // Invert because VStack top = max, bottom = min
        return CGFloat(1.0 - (newestShares - sharesRange.min) / range)
    }

    private var rightYAxisLabels: some View {
        ZStack(alignment: .leading) {
            // Static axis labels
            VStack {
                Text(formatSharesValue(sharesRange.max))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                let midValue = (sharesRange.max + sharesRange.min) / 2
                Text(formatSharesValue(midValue))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                Text(formatSharesValue(sharesRange.min))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Highlighted newest shares value positioned at the dashed line
            GeometryReader { geometry in
                let yPos = newestSharesYPosition * geometry.size.height
                Text(formatSharesValue(newestShares))
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(AppColors.confidenceSharesOutstanding)
                    .fixedSize()
                    .position(x: geometry.size.width / 2, y: yPos)
            }
        }
        .frame(height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    // MARK: - X-Axis Labels (scrollable, positioned via GeometryReader)

    private var scrollableXAxisLabels: some View {
        GeometryReader { geometry in
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(dataPoint.period)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .position(
                        x: columnWidth * CGFloat(index) + columnWidth / 2,
                        y: 10
                    )
            }
        }
        .frame(height: 20)
    }

    // MARK: - Data Label Rows

    private var dividendLabels: some View {
        GeometryReader { geometry in
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(viewType == .yield
                     ? String(format: "%.2f%%", dataPoint.dividendYield)
                     : formatLargeNumber(dataPoint.dividendAmount))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(AppColors.confidenceDividends)
                    .lineLimit(1)
                    .fixedSize()
                    .position(
                        x: columnWidth * CGFloat(index) + columnWidth / 2,
                        y: 10
                    )
            }
        }
        .frame(height: labelRowHeight)
    }

    private var buybackLabels: some View {
        GeometryReader { geometry in
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(viewType == .yield
                     ? String(format: "%.2f%%", dataPoint.buybackYield)
                     : formatLargeNumber(dataPoint.buybackAmount))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(AppColors.confidenceBuybacks)
                    .lineLimit(1)
                    .fixedSize()
                    .position(
                        x: columnWidth * CGFloat(index) + columnWidth / 2,
                        y: 10
                    )
            }
        }
        .frame(height: labelRowHeight)
    }

    private var sharesOutstandingLabels: some View {
        GeometryReader { geometry in
            let chartWidth = geometry.size.width
            let columnWidth = chartWidth / CGFloat(dataPoints.count)

            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(formatSharesValue(dataPoint.sharesOutstanding))
                    .font(.system(size: 11, weight: .regular))
                    .foregroundColor(AppColors.confidenceSharesOutstanding)
                    .lineLimit(1)
                    .fixedSize()
                    .position(
                        x: columnWidth * CGFloat(index) + columnWidth / 2,
                        y: 10
                    )
            }
        }
        .frame(height: labelRowHeight)
    }

    // MARK: - Helper Functions

    /// Normalize shares outstanding to fit within the bar chart's value range
    private func normalizeShares(_ shares: Double) -> Double {
        let range = sharesRange.max - sharesRange.min
        guard range > 0 else { return maxBarValue * 0.5 }

        let normalizedShares = (shares - sharesRange.min) / range // 0 to 1
        let targetMin = maxBarValue * 0.15
        let targetMax = maxBarValue * 0.85
        return targetMin + normalizedShares * (targetMax - targetMin)
    }

    private func formatLeftAxisValue(_ value: Double) -> String {
        switch viewType {
        case .yield:
            return String(format: "%.1f%%", value)
        case .capital:
            return formatLargeNumber(value)
        }
    }

    private func formatSharesValue(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "%.2fB", value / 1000)
        } else if value >= 100 {
            return String(format: "%.0fM", value)
        } else if value >= 10 {
            return String(format: "%.1fM", value)
        } else {
            return String(format: "%.2fM", value)
        }
    }

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000 {
            return String(format: "$%.0fT", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "$%.0fB", number / 1_000)
        } else {
            return String(format: "$%.0fM", number)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            Text("Yield View")
                .foregroundColor(.white)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .yield
            )

            Divider()

            Text("Capital View")
                .foregroundColor(.white)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .capital
            )
        }
        .padding()
    }
}
