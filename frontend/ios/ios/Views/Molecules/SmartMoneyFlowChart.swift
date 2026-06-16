//
//  SmartMoneyFlowChart.swift
//  ios
//
//  Molecule: Two-chart view for Smart Money tracking
//  Top: Stock price line chart to show price movement
//  Bottom: Buy/Sell volume bar chart to show smart money activity
//  Users can compare when smart money bought/sold relative to price movements
//

import SwiftUI
import Charts

struct SmartMoneyFlowChart: View {
    let priceData: [StockPriceDataPoint]
    let dailyPrices: [DailyPricePoint]
    let flowData: [SmartMoneyFlowDataPoint]
    /// When false, only the buy/sell volume bars render (no price line on
    /// top) — for a caller that already shows the price right alongside.
    /// Defaults true; the Holders tab and the Ticker Report's insider chart
    /// both overlay the price line on the bars.
    var showPriceChart: Bool = true
    /// When false, the volume bars hide their trailing magnitude y-axis (the
    /// net-flow badge conveys the totals instead). Lets the report align the
    /// bars under the analyst price line. Defaults true for the Holders tab.
    var showVolumeYAxis: Bool = true

    // Chart configuration
    private let priceChartHeight: CGFloat = 80
    private let volumeChartHeight: CGFloat = 145

    /// Wider bars for quarterly (8 bars) vs monthly (12 bars)
    private var barWidth: CGFloat {
        isQuarterly ? 20 : 12
    }

    /// Whether we have enough daily data for a detailed price line
    private var useDailyPrices: Bool {
        dailyPrices.count >= 10
    }

    /// All month labels from the data
    private var allMonths: [String] {
        flowData.map { $0.month }
    }

    /// Whether data uses quarterly keys (e.g. "Q1\n'24") vs monthly ("MM/YYYY")
    private var isQuarterly: Bool {
        flowData.first?.month.hasPrefix("Q") == true
    }

    /// Show all labels for quarterly (≤8 bars), sparse for monthly (12 bars)
    private var xAxisLabels: [String] {
        if isQuarterly { return allMonths }
        let n = flowData.count
        guard n >= 4 else { return allMonths }
        // 4 evenly-spaced month labels (far-left, ~1/3, ~2/3, far-right) — was 3.
        let idxs = Array(Set((0..<4).map {
            Int((Double($0) * Double(n - 1) / 3.0).rounded())
        })).sorted()
        return idxs.map { flowData[$0].month }
    }

    /// Format label for x-axis display
    private func formatMonthLabel(_ month: String) -> String {
        // Quarterly keys like "Q1\n'24" — return as-is
        if month.hasPrefix("Q") { return month }

        // Monthly keys: "MM/YYYY" → "MM/YY"
        let components = month.split(separator: "/")
        guard components.count == 2,
              let year = components.last,
              year.count == 4 else {
            return month
        }
        let shortYear = year.suffix(2)
        return "\(components[0])/\(shortYear)"
    }

    var body: some View {
        VStack(spacing: 0) {
            // Top: Stock Price Line Chart (optional — hidden in the report's
            // merged chart where the price line lives above).
            if showPriceChart {
                if useDailyPrices {
                    dailyPriceChart
                } else {
                    monthlyPriceChart
                }
            }

            // Bottom: Buy/Sell Volume Bar Chart
            volumeChart
        }
    }

    // MARK: - Daily Price Chart (detailed, like Earnings)

    private var dailyPriceChart: some View {
        Chart {
            // Area fill under the daily line
            ForEach(Array(dailyPrices.enumerated()), id: \.element.id) { index, point in
                AreaMark(
                    x: .value("Day", index),
                    yStart: .value("Base", dailyPriceRange.min),
                    yEnd: .value("Price", point.price)
                )
                .foregroundStyle(
                    LinearGradient(
                        colors: [
                            HoldersColors.flowLine.opacity(0.4),
                            HoldersColors.flowLine.opacity(0.05)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .interpolationMethod(.catmullRom)
            }

            // Daily price line
            ForEach(Array(dailyPrices.enumerated()), id: \.element.id) { index, point in
                LineMark(
                    x: .value("Day", index),
                    y: .value("Price", point.price)
                )
                .foregroundStyle(HoldersColors.flowLine)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.catmullRom)
            }
        }
        .chartXAxis(.hidden)
        .chartXScale(domain: 0...(dailyPrices.count - 1), range: .plotDimension(padding: barWidth / 2))
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine()
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                AxisValueLabel {
                    if let doubleValue = value.as(Double.self) {
                        Text(formatPriceValue(doubleValue))
                            .font(AppTypography.caption)
                            .foregroundStyle(AppColors.textMuted)
                    }
                }
            }
        }
        .chartYScale(domain: dailyPriceRange.min...dailyPriceRange.max)
        .chartPlotStyle { plotArea in
            plotArea.background(Color.clear)
        }
        .frame(height: priceChartHeight)
    }

    // MARK: - Monthly Price Chart (fallback)

    private var monthlyPriceChart: some View {
        Chart {
            ForEach(priceData) { point in
                AreaMark(
                    x: .value("Month", point.month),
                    yStart: .value("Base", priceRange.min),
                    yEnd: .value("Price", point.price)
                )
                .foregroundStyle(
                    LinearGradient(
                        colors: [
                            HoldersColors.flowLine.opacity(0.4),
                            HoldersColors.flowLine.opacity(0.1)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .interpolationMethod(.catmullRom)
            }

            ForEach(priceData) { point in
                LineMark(
                    x: .value("Month", point.month),
                    y: .value("Price", point.price)
                )
                .foregroundStyle(HoldersColors.flowLine)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.catmullRom)
            }
        }
        .chartXAxis(.hidden)
        .chartXScale(domain: allMonths, range: .plotDimension(padding: barWidth / 2))
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine()
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                AxisValueLabel {
                    if let doubleValue = value.as(Double.self) {
                        Text(formatPriceValue(doubleValue))
                            .font(AppTypography.caption)
                            .foregroundStyle(AppColors.textMuted)
                    }
                }
            }
        }
        .chartYScale(domain: priceRange.min...priceRange.max)
        .chartPlotStyle { plotArea in
            plotArea.background(Color.clear)
        }
        .frame(height: priceChartHeight)
    }

    // MARK: - Volume Chart (Bottom)

    private var volumeChart: some View {
        Chart {
            // Buy volume bars (positive, green) - above zero line
            ForEach(flowData) { point in
                BarMark(
                    x: .value("Month", point.month),
                    y: .value("Buy", displayVolume(point.buyVolume)),
                    width: .fixed(barWidth)
                )
                .foregroundStyle(HoldersColors.buyVolume)
                .cornerRadius(2)
                // Outlier label sits ABOVE a (positive) buy bar — outside the
                // column, not overlapping it — with the ↑ pointing to the
                // clipped top edge.
                .annotation(position: .top, spacing: 2) {
                    clippedBarLabel(point.buyVolume, arrow: "↑")
                }
            }

            // Sell volume bars (negative, red) - below zero line
            ForEach(flowData) { point in
                BarMark(
                    x: .value("Month", point.month),
                    y: .value("Sell", -displayVolume(point.sellVolume)),
                    width: .fixed(barWidth)
                )
                .foregroundStyle(HoldersColors.sellVolume)
                .cornerRadius(2)
                // Outlier label sits BELOW a (negative) sell bar — at the bottom
                // of the column, above the month axis label, not overlapping the
                // bar — with the ↓ pointing to the clipped bottom edge.
                .annotation(position: .bottom, spacing: 2) {
                    clippedBarLabel(point.sellVolume, arrow: "↓")
                }
            }

            // Zero line
            RuleMark(y: .value("Zero", 0))
                .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                .lineStyle(StrokeStyle(lineWidth: 0.5))

            // No-activity months — a thin gray dash at the zero line so an empty
            // month reads as "no trades that month", not a rendering gap.
            ForEach(flowData.filter { $0.buyVolume <= 0 && $0.sellVolume <= 0 }) { point in
                RectangleMark(
                    x: .value("Month", point.month),
                    y: .value("Zero", 0),
                    width: .fixed(barWidth),
                    height: .fixed(2)
                )
                .foregroundStyle(AppColors.textMuted.opacity(0.55))
                .cornerRadius(1)
            }
        }
        .chartXScale(domain: allMonths, range: .plotDimension(padding: barWidth / 2))
        .chartXAxis {
            AxisMarks(values: xAxisLabels) { value in
                AxisValueLabel {
                    if let stringValue = value.as(String.self) {
                        Text(formatMonthLabel(stringValue))
                            // size 10 to match the Capital Allocation chart axis.
                            .font(.system(size: 11))
                            .foregroundStyle(AppColors.textMuted)
                    }
                }
            }
        }
        .chartYAxis {
            if showVolumeYAxis {
                AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                    AxisValueLabel {
                        if let doubleValue = value.as(Double.self) {
                            Text(formatVolumeValue(doubleValue))
                                // size 10 to match the Capital Allocation chart axis.
                                .font(.system(size: 11))
                                .foregroundStyle(AppColors.textMuted)
                        }
                    }
                }
            }
        }
        .chartYScale(domain: -yScaleMax...yScaleMax)
        .chartPlotStyle { plotArea in
            plotArea.background(Color.clear)
        }
        .frame(height: volumeChartHeight)
    }

    // MARK: - Computed Properties

    private var priceRange: (min: Double, max: Double) {
        let prices = priceData.map { $0.price }
        let minPrice = (prices.min() ?? 0)
        let maxPrice = (prices.max() ?? 1)
        let padding = (maxPrice - minPrice) * 0.1
        return (minPrice - padding, maxPrice + padding)
    }

    private var dailyPriceRange: (min: Double, max: Double) {
        let prices = dailyPrices.map { $0.price }
        let minPrice = (prices.min() ?? 0)
        let maxPrice = (prices.max() ?? 1)
        let padding = (maxPrice - minPrice) * 0.1
        return (minPrice - padding, maxPrice + padding)
    }

    // One month can dwarf the other 11 — e.g. an executive's planned
    // multi-million-share sale among otherwise sub-150K months. Scaling the
    // axis to that single outlier squashes every normal month onto the floor,
    // so they all read as identical slivers (the bug this chart "looked like").
    // Fix: when the largest magnitude exceeds `outlierFactor`× the next, scale
    // the axis to the SECOND-largest. The outlier then overflows the axis,
    // renders clipped at the edge, and carries a value label — while the normal
    // months reclaim the full chart height and become legible.
    private static let outlierFactor: Double = 4

    /// Y-axis half-extent (±). Outlier-aware: drops the one dominant bar from
    /// the scale so the rest are readable. Falls back to the old
    /// `largest × 1.15` when there is no dominant outlier, so normal tickers
    /// render exactly as before.
    private var axisMax: Double {
        let mags = flowData
            .flatMap { [$0.buyVolume, $0.sellVolume] }
            .filter { $0 > 0 }
            .sorted(by: >)
        guard let largest = mags.first else { return 1 }
        if mags.count >= 2 {
            let second = mags[1]
            if second > 0 && largest > Self.outlierFactor * second {
                return second * 1.30   // fit the rest; let the outlier clip
            }
        }
        return largest * 1.15
    }

    /// True when a bar's real magnitude overflows the (outlier-capped) axis —
    /// it draws clipped at the edge and shows its true value as a label.
    private func isClipped(_ value: Double) -> Bool {
        value > axisMax
    }

    /// True when ANY bar overflows the outlier-capped axis (its true magnitude
    /// is drawn as a label just OUTSIDE the bar). Drives the extra y-scale
    /// headroom below so that label doesn't collide with the month-axis labels.
    private var hasClippedBar: Bool {
        flowData.contains { isClipped($0.buyVolume) || isClipped($0.sellVolume) }
    }

    /// Y half-extent for the SCALE — distinct from `axisMax`, which caps the bar
    /// HEIGHT. A clipped outlier's value label sits just outside the bar (.top
    /// for buys, .bottom for sells); without headroom it spills onto the month
    /// labels. Extending the scale past the bar cap parks the clipped tip short
    /// of the plot edge, leaving room for the label INSIDE the plot. No clip →
    /// unchanged, so normal tickers render exactly as before.
    private var yScaleMax: Double {
        hasClippedBar ? axisMax * 1.30 : axisMax
    }

    // A month with real but tiny volume — e.g. a single 480-share insider buy
    // among millions of shares of selling — would round to a 0-height bar and
    // vanish. Floor every NON-ZERO bar to this fraction of the axis so "activity
    // happened" is always visible. Magnitudes below the floor all render at the
    // SAME minimal height (a presence marker, not a true-to-scale bar). Truly-
    // empty months stay at 0 and get the gray no-activity dash.
    private static let minBarHeightRatio: Double = 0.04

    /// Plotted bar height: the true value, floored to a visible minimum when
    /// non-zero, and capped at the axis so a dominant outlier clips cleanly at
    /// the edge (its true magnitude is shown via the clipped-bar label). Zero
    /// stays zero.
    private func displayVolume(_ value: Double) -> Double {
        guard value > 0 else { return 0 }
        let floored = max(value, axisMax * Self.minBarHeightRatio)
        return min(floored, axisMax)
    }

    // MARK: - Formatting

    private func formatPriceValue(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "$%.0fK", value / 1000)
        }
        return String(format: "$%.0f", value)
    }

    private func formatVolumeValue(_ value: Double) -> String {
        let absValue = abs(value)
        if absValue >= 1000 {
            return String(format: "%.0fB", value / 1000)
        } else if absValue >= 1 {
            return String(format: "%.0fM", value)
        } else if absValue >= 0.01 {
            // Share count in thousands — NOT dollars (these bars are Form-4
            // share volumes). The old "$%.0fK" mislabeled e.g. 100K shares as
            // "$100K".
            return String(format: "%.0fK", value * 1000)
        } else if absValue > 0 {
            return String(format: "%.1fK", value * 1000)
        }
        return "0"
    }

    /// One-decimal magnitude for the clipped-bar label so a dominant outlier
    /// reads "8.7M" rather than `formatVolumeValue`'s rounded "9M".
    private func formatVolumeLabel(_ value: Double) -> String {
        let absValue = abs(value)
        if absValue >= 1000 {
            return String(format: "%.1fB", value / 1000)
        } else if absValue >= 1 {
            return String(format: "%.1fM", value)
        } else if absValue >= 0.01 {
            return String(format: "%.0fK", value * 1000)
        }
        return formatVolumeValue(value)
    }

    /// Label drawn only on a bar that overflows the outlier-capped axis: its
    /// TRUE magnitude plus an arrow toward the clipped edge (e.g. "8.7M↓").
    /// White for contrast against the colored bar; empty for normal bars so
    /// every other month renders unchanged.
    @ViewBuilder
    private func clippedBarLabel(_ value: Double, arrow: String) -> some View {
        if isClipped(value) {
            Text("\(formatVolumeLabel(value))\(arrow)")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.white)
                .fixedSize()
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            // Outlier case (ORCL): one ~8.7M month clips at the edge with a
            // value label; the 11 normal months stay legible.
            Text("Dominant-outlier month")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            SmartMoneyFlowChart(
                priceData: [],
                dailyPrices: [],
                flowData: SmartMoneyFlowDataPoint.insiderOutlierSampleData,
                showPriceChart: false
            )

            // Normal case: no outlier → renders exactly as before.
            Text("Smart Money vs Price")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            SmartMoneyFlowChart(
                priceData: StockPriceDataPoint.sampleData,
                dailyPrices: [],
                flowData: SmartMoneyFlowDataPoint.insiderSampleData
            )
        }
        .padding()
    }
}
