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

    /// Every quarter has cash-flow figures and none returned a dollar: the bar band is empty
    /// on purpose, and says so (`noCapitalReturnNote`). Decided from the CASH in both views
    /// (`SignalOfConfidenceScale.returnedNothing`) — a rounded or unpriced 0.00% yield is
    /// not "no dividends or buybacks".
    private var returnedNothing: Bool {
        SignalOfConfidenceScale.returnedNothing(dataPoints)
    }

    /// Y domain for the stacked bars — always starts at 0 (bars grow from the
    /// baseline), is never zero-width, and never tops out below `materialityFloor`.
    private var barDomain: ClosedRange<Double> {
        let safe = ChartDomain.make(
            barTotals, includeZero: true, headroomFraction: 0.15, fallback: 0...1
        )
        let natural = Swift.max(safe.upperBound, ChartDomain.minimumSpan)
        return 0...Swift.max(natural, materialityFloor)
    }

    /// The axis top below which an amount is not material, so it is never stretched to fill
    /// the chart — the ONE rule both Signal of Confidence charts use
    /// (`SignalOfConfidenceScale.materialityFloor`).
    private var materialityFloor: Double {
        SignalOfConfidenceScale.materialityFloor(for: dataPoints, viewType: viewType)
    }

    private var maxBarValue: Double { barDomain.upperBound }

    private var sharesRange: (min: Double, max: Double) {
        // compactMap: an unreported quarter is nil, and must be absent from the
        // range rather than dragging it to zero.
        let shares = dataPoints.compactMap { $0.sharesOutstanding }.filter { $0.isFinite }
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
            // Over the visible plot, outside the horizontal scroll so it stays in view.
            .overlay(alignment: .top) {
                noCapitalReturnNote
            }

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

            // Dividend bars. EVERY point draws, including a quarter with no cash-flow
            // filing on record (`cashFlowReported == false`, 0.0 placeholders → a
            // zero-height bar, exactly how a measured zero looks). Never filter these
            // ForEach loops: with no `.chartXScale(domain:)`, Swift Charts orders the
            // category axis by first appearance, so a skipped period would move its column
            // behind the shares line and every index-positioned label row below would
            // drift off its bar. The "—" lives in the label rows instead.
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

            // Shares outstanding line. Only quarters that actually REPORTED a share
            // count are plotted; an unreported one is nil and leaves a gap, because
            // drawing it at 0 read as the company retiring every share.
            ForEach(dataPoints.filter { $0.sharesOutstanding != nil }) { dataPoint in
                LineMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding ?? 0))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.linear)
            }

            // Shares outstanding points (reported quarters only — see the line above)
            ForEach(dataPoints.filter { $0.sharesOutstanding != nil }) { dataPoint in
                PointMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding ?? 0))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .symbolSize(50)
            }

            // Dashed connector line from newest shares outstanding to right Y-axis
            // Anchor on the newest REPORTED quarter, not simply the last one — the same
            // value the highlighted right-axis label prints.
            if let lastShares = newestShares {
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
                // Defense in depth for the TestFlight overflow class: no mark may draw
                // outside the plot. Bars sit inside `barDomain` by construction today, but
                // two points sharing one period label land in ONE category column, and a
                // future mark must not be able to paint over the label rows below.
                // Clipping the PLOT (not the whole chart) leaves the axes untouched.
                .clipped()
        }
    }

    // MARK: - Y-Axis Labels

    private var leftYAxisLabels: some View {
        // The bar axis measures dividends + buybacks. When no bar has height the
        // domain is synthetic (needed to keep the scale non-degenerate), so printing
        // "0.9% / 0.6% / 0.3%" off it would invent gradations that describe nothing:
        // the ticks go. When the company returned no CASH at all (`returnedNothing`)
        // the baseline goes too — `noCapitalReturnNote` says what the empty band
        // means, and the shares-outstanding line (right axis) is still real.
        VStack {
            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.9) : "")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                // "2.0%" is wider than the axis column and wrapped onto two lines
                // ("2.0" over "%"); shrink instead of wrapping.
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            Spacer()

            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.6) : "")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            Spacer()

            Text(hasCapitalReturn ? formatLeftAxisValue(maxBarValue * 0.3) : "")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            Spacer()

            // The baseline goes too when nothing was returned: a lone "$0" under an empty
            // band read as a scale with nothing on it. The band's note says what it means.
            // Only then — a Yield view whose cash was real but rounds to 0.00% keeps its "0%".
            Text(returnedNothing ? "" : (viewType == .yield ? "0%" : "$0"))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    /// Says what an empty bar band means when every quarter has cash-flow figures and none
    /// returned anything — "no dividends or buybacks", a measured fact, not missing data.
    /// Sits in the plot's bottom band, below the shares line (drawn inside 15–85% of the
    /// height), where the bars would be. Not hit-testable, so the chart still scrolls.
    @ViewBuilder
    private var noCapitalReturnNote: some View {
        if returnedNothing {
            Text("No dividends or buybacks in these quarters")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.8)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.bottom, AppSpacing.xs)
                .frame(maxWidth: .infinity)
                .frame(height: chartHeight, alignment: .bottom)
                .allowsHitTesting(false)
        }
    }

    /// Every finite share count a quarter actually REPORTED, oldest first.
    private var reportedShares: [Double] {
        dataPoints.compactMap { $0.sharesOutstanding }.filter { $0.isFinite }
    }

    /// False when no quarter reported a share count — the right axis then has nothing
    /// real to label, and `sharesRange` is only a synthetic 0…1 kept for the normaliser.
    private var hasReportedShares: Bool { !reportedShares.isEmpty }

    /// Newest REPORTED share count for the right Y-axis highlight, or nil.
    ///
    /// Was `dataPoints.last?.sharesOutstanding ?? 0`. The backend sends nil for a quarter
    /// whose filing reported no count (FMP's `weightedAverageShsOut: 0`, live on CD's
    /// 2026-06-30 quarter), so the `?? 0` printed a bold "0.00M" — a company with no
    /// shares — exactly on top of the axis-minimum label, while the dashed connector
    /// pointed at the last real count. `CapitalAllocationMiniChart` already read it this way.
    private var newestShares: Double? { reportedShares.last }

    /// A static right-axis label closer than this to the highlighted newest one is hidden
    /// rather than overprinted. Each label is ~13pt tall at the default size, so the old 14pt
    /// let them touch: TestFlight 1.0 (11), CRWV's "566M" axis top sat 16pt above the bold
    /// "551M" and read as one smudge. 20pt leaves a visible gap, and it grows with the
    /// caption it separates (28pt at the reading cap).
    private var axisLabelMinSeparation: CGFloat {
        AppTypography.scaledSize(20, .caption2, maxScale: AppTypography.readingCap)
    }

    private var rightYAxisLabels: some View {
        // Every label — the static max/mid/min AND the highlighted newest — is
        // positioned with `sharesYFractionFromTop`, the exact inverse of the
        // mapping the shares LINE is drawn with. Previously the static labels
        // were laid out flush over the full height (implying a full-height
        // linear axis) while the line was compressed into the 15–85% band, so
        // reading the line against this axis gave the wrong share count for
        // every point except the midpoint.
        //
        // No reported share count anywhere → no labels at all (an empty frame of the same
        // height keeps the plot's width). The synthetic 0…1 range used to print
        // "1.00M / 0.50M / 0.00M" plus a bold "0.00M" for a line that is never drawn.
        GeometryReader { geometry in
            if hasReportedShares {
                let midValue = (sharesRange.max + sharesRange.min) / 2
                ZStack(alignment: .leading) {
                    if !collidesWithNewest(sharesRange.max, in: geometry) {
                        axisLabel(
                            formatSharesValue(sharesRange.max), value: sharesRange.max,
                            in: geometry, color: AppColors.textMuted, bold: false
                        )
                    }
                    if !collidesWithNewest(midValue, in: geometry) {
                        axisLabel(
                            formatSharesValue(midValue), value: midValue,
                            in: geometry, color: AppColors.textMuted, bold: false
                        )
                    }
                    if !collidesWithNewest(sharesRange.min, in: geometry) {
                        axisLabel(
                            formatSharesValue(sharesRange.min), value: sharesRange.min,
                            in: geometry, color: AppColors.textMuted, bold: false
                        )
                    }
                    // Highlighted newest REPORTED shares value, on the dashed connector.
                    if let newest = newestShares {
                        axisLabel(
                            formatSharesValue(newest), value: newest,
                            in: geometry, color: AppColors.confidenceSharesOutstanding, bold: true
                        )
                    }
                }
            } else {
                Color.clear
            }
        }
        .frame(height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    /// True when a static label at `value` would sit within `axisLabelMinSeparation` of
    /// the highlighted newest label — it is then hidden, the way
    /// `CapitalAllocationMiniChart.sharesTicks` replaces a colliding tick.
    private func collidesWithNewest(_ value: Double, in geometry: GeometryProxy) -> Bool {
        guard let newest = newestShares else { return false }
        let dy = abs(sharesYFractionFromTop(value) - sharesYFractionFromTop(newest))
            * geometry.size.height
        return dy < axisLabelMinSeparation
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
                // "—" rather than "0.00%" / "$0M": no cash-flow filing is on record for the
                // quarter, so its 0.0 is a placeholder, not a measured "paid nothing".
                Text(dataPoint.cashFlowReported
                     ? (viewType == .yield
                        ? String(format: "%.2f%%", dataPoint.dividendYield)
                        : formatLargeNumber(dataPoint.dividendAmount))
                     : "—")
                    .font(.system(size: 11, weight: .semibold))
                    // Text-safe sibling of the series token: a readable number needs 4.5
                    // and `confidenceDividends` is a 3:1 graphic (4.27:1 on the dark card,
                    // 3.84:1 nested). Colour stays with the bar/line.
                    .foregroundColor(AppColors.primaryBlue)
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
                // "—" for a quarter with no cash-flow filing on record (see dividendLabels).
                Text(dataPoint.cashFlowReported
                     ? (viewType == .yield
                        ? String(format: "%.2f%%", dataPoint.buybackYield)
                        : formatLargeNumber(dataPoint.buybackAmount))
                     : "—")
                    .font(.system(size: 11, weight: .semibold))
                    // Text-safe sibling of `confidenceBuybacks` (3.30:1 light card).
                    .foregroundColor(AppColors.gain)
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
                // "—" rather than "0M": the filing did not report it.
                Text(dataPoint.sharesOutstanding.map(formatSharesValue) ?? "—")
                    .font(.system(size: 11, weight: .regular))
                    // Text-safe sibling of `confidenceSharesOutstanding` (3.57:1 light card).
                    .foregroundColor(AppColors.accentYellow)
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

    /// Amounts arrive in USD millions. ONE rule shared with `CapitalAllocationMiniChart`:
    /// this used `$%.0fB`, so $1,499M / $1,500M read "$1B" / "$2B" here and "$1.5B" in the
    /// report for the same quarter.
    private func formatLargeNumber(_ number: Double) -> String {
        SignalOfConfidenceFormat.money(millions: number)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
        VStack(spacing: AppSpacing.xl) {
            Text("Yield View")
                .foregroundColor(AppColors.textOnAccent)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .yield
            )

            Divider()

            Text("Capital View")
                .foregroundColor(AppColors.textOnAccent)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .capital
            )

            // The newest quarter reported no share count (CD, 2026-06-30): the bold
            // right-axis label must be the last REAL count, never "0.00M".
            Text("Newest share count unreported")
                .foregroundColor(AppColors.textPrimary)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceChartPreviewData.newestSharesMissing,
                viewType: .capital
            )

            // An INTERIOR quarter (Q4 '24) has no cash-flow filing on record: its bars
            // are zero-height in place, its dividend and buyback cells read "—" (never
            // "0.00%" / "$0M"), and every column stays under its own labels.
            Text("Interior cash-flow gap — Yield")
                .foregroundColor(AppColors.textPrimary)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleInteriorCashFlowGap.dataPoints,
                viewType: .yield
            )
            Text("Interior cash-flow gap — Capital")
                .foregroundColor(AppColors.textPrimary)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleInteriorCashFlowGap.dataPoints,
                viewType: .capital
            )
        }
        .padding()
        }
    }
}

#Preview("No / immaterial capital return") {
    ScrollView {
        VStack(spacing: AppSpacing.xl) {
            // Nothing returned in any quarter: no left-axis labels, the band's note, "$0"
            // cells (never "$0M"), and two unreported share counts skipped by the line.
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleNoCapitalReturnDiluting.dataPoints,
                viewType: .capital
            )

            // One immaterial quarter: the scale floor keeps a $2.5M bar on a $45B company
            // the sliver it is (axis ≈ $0–$113M), never a full-height bar on a "$0–$3M" axis.
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceChartPreviewData.immaterialAmount,
                viewType: .capital
            )
        }
        .padding()
    }
    .background(AppColors.background)
}

/// Preview-only points (sample shapes, not market data).
private enum SignalOfConfidenceChartPreviewData {
    static var newestSharesMissing: [SignalOfConfidenceDataPoint] {
        var points = Array(SignalOfConfidenceSectionData.sampleData.dataPoints.prefix(4))
        points.append(SignalOfConfidenceDataPoint(
            period: "Q2 '25",
            dividendYield: 1.4,
            buybackYield: 1.2,
            dividendAmount: 1_499,
            buybackAmount: 12_300,
            sharesOutstanding: nil
        ))
        return points
    }

    /// The non-returner series with one immaterial $2.5M quarter (sample shapes).
    static var immaterialAmount: [SignalOfConfidenceDataPoint] {
        SignalOfConfidenceSectionData.sampleNoCapitalReturnDiluting.dataPoints.map { point -> SignalOfConfidenceDataPoint in
            guard point.period == "Q2 '25" else { return point }
            return SignalOfConfidenceDataPoint(
                period: point.period,
                dividendYield: 0.01,
                buybackYield: 0,
                dividendAmount: 2.5,
                buybackAmount: 0,
                sharesOutstanding: point.sharesOutstanding,
                marketCap: point.marketCap
            )
        }
    }
}
