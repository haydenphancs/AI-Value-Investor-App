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

    /// Stacked bar total per quarter, for the selected view type.
    private var barTotals: [Double] {
        switch viewType {
        case .yield:
            return dataPoints.map { $0.dividendYield + $0.buybackYield }
        case .capital:
            return dataPoints.map { $0.dividendAmount + $0.buybackAmount }
        }
    }

    /// True when at least one quarter returned capital. A company that pays no
    /// dividend AND buys back no stock (very common) has all-zero totals — the
    /// old `.max() ?? 1` never fired, giving `maxBarValue == 0`, a
    /// `0...0` chart scale, three identical grid values under
    /// `ForEach(id: \.self)`, and a zero denominator in the shares normaliser.
    private var hasCapitalReturn: Bool { barTotals.contains { $0 > 0 } }

    /// Y domain for the stacked bars — always starts at 0 (bars grow from the
    /// baseline) and is never zero-width.
    private var barDomain: ClosedRange<Double> {
        let safe = ChartDomain.make(
            barTotals, includeZero: true, headroomFraction: 0.15, fallback: 0...1
        )
        return 0...Swift.max(safe.upperBound, ChartDomain.minimumSpan)
    }

    private var maxBarValue: Double { barDomain.upperBound }

    private var sharesRange: (min: Double, max: Double) {
        let shares = dataPoints.map { $0.sharesOutstanding }.filter { $0.isFinite }
        guard let lo = shares.min(), let hi = shares.max(), hi > 0 else {
            return (0, 1)
        }
        // Additive padding: a share count is always positive here, but keep the
        // range strictly non-degenerate when every quarter has identical shares.
        let pad = max((hi - lo) * 0.1, hi * 0.01)
        return (max(lo - pad, 0), hi + pad)
    }

    // Grid line values (3 interior lines). Distinct by construction, so they
    // stay valid `ForEach(id: \.self)` identities.
    private var gridValues: [Double] {
        ChartDomain.gridValues(in: barDomain, count: 3)
    }

    /// Total height of x-axis + data label rows below the chart
    private var belowChartHeight: CGFloat {
        // x-axis row + padding + (label row + padding) * 3
        labelRowHeight + AppSpacing.sm + (labelRowHeight + AppSpacing.sm) * labelRowCount
    }

    /// Bar width. Was `count > 6 ? .fixed(18) : .fixed(18)` — both branches
    /// identical, so with 12+ quarters the two 18pt bars overflowed a column
    /// narrower than 36pt and bled into the neighbouring period.
    private var barWidth: CGFloat {
        dataPoints.count > Int(visibleColumnCount) ? 12 : 18
    }

    var body: some View {
        if dataPoints.isEmpty {
            ChartUnavailableView(
                message: "Dividend and buyback history isn't available for this company.",
                systemImage: "chart.bar.xaxis"
            )
            .frame(height: chartHeight)
        } else {
            chartBody
        }
    }

    private var chartBody: some View {
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
                    width: .fixed(barWidth)
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
                    width: .fixed(barWidth)
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
        .chartYScale(domain: barDomain)
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
    }

    // MARK: - Y-Axis Labels

    private var leftYAxisLabels: some View {
        // The bar axis measures dividends + buybacks. When the company returned
        // NO capital in any quarter the domain is a synthetic 0...1 (needed to
        // keep the scale non-degenerate), so printing "0.9% / 0.6% / 0.3%" off
        // it would invent gradations that describe nothing. Show only the
        // baseline in that case — the shares-outstanding line is still real.
        VStack {
            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.9) : "")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.6) : "")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.3) : "")
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
        sharesYFractionFromTop(newestShares)
    }

    private var rightYAxisLabels: some View {
        // Every label — the static max/mid/min AND the highlighted newest — is
        // positioned with `sharesYFractionFromTop`, the exact inverse of the
        // mapping the shares LINE is drawn with. Previously the static labels
        // were laid out flush over the full height (implying a full-height
        // linear axis) while the line was compressed into the 15–85% band, so
        // reading the line against this axis gave the wrong share count for
        // every point except the midpoint.
        GeometryReader { geometry in
            let midValue = (sharesRange.max + sharesRange.min) / 2
            ZStack(alignment: .leading) {
                axisLabel(
                    formatSharesValue(sharesRange.max), value: sharesRange.max,
                    in: geometry, color: AppColors.textMuted, bold: false
                )
                axisLabel(
                    formatSharesValue(midValue), value: midValue,
                    in: geometry, color: AppColors.textMuted, bold: false
                )
                axisLabel(
                    formatSharesValue(sharesRange.min), value: sharesRange.min,
                    in: geometry, color: AppColors.textMuted, bold: false
                )
                // Highlighted newest shares value, sitting on the dashed connector
                axisLabel(
                    formatSharesValue(newestShares), value: newestShares,
                    in: geometry, color: AppColors.confidenceSharesOutstanding, bold: true
                )
            }
        }
        .frame(height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    private func axisLabel(
        _ text: String, value: Double, in geometry: GeometryProxy,
        color: Color, bold: Bool
    ) -> some View {
        Text(text)
            .font(bold ? .system(size: 11, weight: .bold) : AppTypography.caption)
            .foregroundColor(color)
            .fixedSize()
            .position(
                x: geometry.size.width / 2,
                y: sharesYFractionFromTop(value) * geometry.size.height
            )
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

    /// Fraction of the plot band (0…1) the shares line is drawn in. The line is
    /// inset so it can't collide with the bars; the right-hand axis labels use
    /// the SAME inset (see `sharesYFractionFromTop`) so a value read off the
    /// axis matches the point plotted on the line.
    private static let sharesBandLow: Double = 0.15
    private static let sharesBandHigh: Double = 0.85

    /// Where `shares` sits within `sharesRange`, 0…1. 0.5 when the range is
    /// degenerate (every quarter identical) so the line is flat and centred.
    private func sharesFraction(_ shares: Double) -> Double {
        let range = sharesRange.max - sharesRange.min
        guard shares.isFinite, range > 0 else { return 0.5 }
        return Swift.min(Swift.max((shares - sharesRange.min) / range, 0), 1)
    }

    /// Normalize shares outstanding into the bar chart's value range.
    private func normalizeShares(_ shares: Double) -> Double {
        let band = Self.sharesBandLow
            + sharesFraction(shares) * (Self.sharesBandHigh - Self.sharesBandLow)
        return maxBarValue * band
    }

    /// Vertical position of `shares` as a fraction from the TOP of the plot —
    /// the inverse of `normalizeShares`, used to place the right-axis labels on
    /// the same scale the line is drawn against.
    private func sharesYFractionFromTop(_ shares: Double) -> CGFloat {
        let band = Self.sharesBandLow
            + sharesFraction(shares) * (Self.sharesBandHigh - Self.sharesBandLow)
        return CGFloat(1.0 - band)
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
