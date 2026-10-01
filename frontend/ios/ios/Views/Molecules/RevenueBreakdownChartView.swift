//
//  RevenueBreakdownChartView.swift
//  ios
//
//  Molecule: Waterfall chart showing revenue sources and cost breakdown
//  Costs descend from top of revenue; loss companies show negative values
//

import SwiftUI

struct RevenueBreakdownChartView: View {
    let data: RevenueBreakdownData

    private let chartHeight: CGFloat = 320
    private let leftAxisWidth: CGFloat = 50
    private let rightAxisWidth: CGFloat = 50
    /// Points reserved beside the net bar for its "Net Profit" / "-Net Loss" caption: the
    /// 2pt gap, one captionSmall line at its 1.4× Dynamic Type cap (~17pt) and the 2pt
    /// minimum bar below. The caption is an OVERLAY — it never sizes its column — so this
    /// reservation is what keeps it inside the plot rather than clipped by it.
    private let captionAllowance: CGFloat = 22
    /// A non-zero net result never vanishes: a 0.5B loss on 50B of revenue is ~2pt tall.
    private let minimumNetBarHeight: CGFloat = 2
    /// Closest two axis labels may sit (one `caption` line) before they print on each other.
    private let minimumLabelGap: CGFloat = 16

    // MARK: - Bounds — sized from what is DRAWN, one rule for profit and loss
    //
    // Three columns are drawn: the revenue stack from 0 up to `totalRevenue`, the cost
    // waterfall from `totalRevenue` DOWN by `drawnWaterfallTotal` (credits are skipped), and
    // the net bar from 0 to `netProfit`. The plot must contain all three plus the net bar's
    // caption. It used to be sized from `max(totalRevenue, totalCosts)` with a floor fixed at
    // 0 in a profit year, so:
    //   • an operating loss rescued by other income (a profit year whose drawn costs exceed
    //     revenue) ran the cost column out of the bottom of the frame, into the legend;
    //   • net income above revenue (a divestiture gain) drew the profit bar ~380pt tall in
    //     a 320pt plot, over the section header; with ZERO revenue the scale fell back to a
    //     constant 1 dollar and the bar was billions of points tall;
    //   • a small loss left no room for its caption, the over-full column was CENTRED by
    //     its frame, and the loss bar floated above the zero line.
    // Bars are now placed absolutely (offsets, not spacers), captions are overlays, and the
    // plot is `.clipped()` as a last line of defence — never as the fix.

    /// Top of the plot, in value units. The caption reservation is closed-form:
    /// (top − NI)·h ≥ l·(top − bottom)  ⇔  top ≥ (NI·h − l·bottom) / (h − l).
    /// Only a PROFIT bar carries its caption above it. NB: in a profit year
    /// `chartBottomValue` does not read this property, so the two never recurse.
    private var chartTopValue: Double {
        let ceiling = max(data.totalRevenue, data.netProfit, data.drawnWaterfallTotal, 0) * 1.1
        guard data.isProfit else { return ceiling }
        let h = Double(chartHeight)
        let l = Double(captionAllowance)
        let needed = (data.netProfit * h - l * chartBottomValue) / (h - l)
        return needed.isFinite ? max(ceiling, needed) : ceiling
    }

    /// Bottom of the plot: below the LOWER of the reported net result and where the drawn
    /// waterfall actually lands — in BOTH branches. The waterfall skips credit lines (income
    /// is not a cost), so with a credit it ends BELOW net income — INTC FY2025 lands at
    /// −1.55B against a −0.27B loss — and in a profit year it can still end below zero.
    /// A loss bar's caption hangs under it; reserved in closed form:
    /// (NI − bottom)·h ≥ l·(top − bottom)  ⇔  bottom ≤ (NI·h − l·top) / (h − l).
    /// NB: in a loss year `chartTopValue` does not read this property.
    private var chartBottomValue: Double {
        let drawnCosts = data.waterfallItems.filter { !$0.isCredit }.reduce(0) { $0 + $1.value }
        let waterfallBottom = data.totalRevenue - drawnCosts
        let floor = min(data.netProfit, waterfallBottom, 0) * 1.2
        guard !data.isProfit else { return floor }
        let h = Double(chartHeight)
        let l = Double(captionAllowance)
        let needed = (data.netProfit * h - l * chartTopValue) / (h - l)
        return needed.isFinite ? min(floor, needed) : floor
    }

    private var chartRange: Double {
        let range = chartTopValue - chartBottomValue
        return range > 0 && range.isFinite ? range : 1 // Prevent division by zero
    }

    /// True when anything is drawn below zero — the plot then carries a zero line, and
    /// every bar stands on it rather than on the frame bottom.
    private var hasNegativeRegion: Bool {
        chartBottomValue < 0
    }

    // Where is the zero line (as fraction from bottom) — derived from the bottom, not from
    // the sign of net income: a profit year can dip below zero too.
    private var zeroLinePosition: CGFloat {
        hasNegativeRegion ? CGFloat(-chartBottomValue / chartRange) : 0
    }

    // Grid values for Y-axis — aligned to NET revenue so 100% is the revenue the company
    // books (profit branch; the loss branch ladders the whole range in quarters). A gross
    // stack (INTC) rises past 100% to 133%; the eliminations step brings the waterfall
    // back down to it.
    private var gridValues: [Double] {
        // The 0–100% ladder needs its quarter steps far enough apart to read: net income
        // can now set the plot top (a gain several times revenue), which would otherwise
        // squeeze all five labels onto the zero line.
        let quarterStep = CGFloat(data.netRevenue * 0.25 / chartRange) * chartHeight
        if data.isProfit && data.netRevenue > 0 && quarterStep >= minimumLabelGap {
            let rev = data.netRevenue
            let ladder = [0, rev * 0.25, rev * 0.5, rev * 0.75, rev]
            // A profit year whose drawn waterfall dips below zero has a region under the
            // zero line: label its floor too, unless it sits so close to the "0" label that
            // the two would print on top of each other.
            let floorGap = zeroLinePosition * chartHeight
            return hasNegativeRegion && floorGap >= minimumLabelGap ? [chartBottomValue] + ladder : ladder
        }
        // No revenue, or a loss: ladder the whole range in quarters. `chartRange` never
        // collapses to 0, so the five lines (and their labels) never stack on one pixel.
        let step = chartRange / 4
        return [
            chartBottomValue,
            chartBottomValue + step,
            chartBottomValue + step * 2,
            chartBottomValue + step * 3,
            chartTopValue
        ]
    }

    // Percentage labels (relative to net revenue), one per grid value.
    //
    // The `totalRevenue <= 0` branch used to return the fixed 0/25/50/75/100%
    // ladder while `gridValues` took the LOSS branch, so the labels asserted
    // percentages that had nothing to do with the lines they were placed on.
    // With no revenue there is no meaningful percentage — show a dash. Every label is
    // computed from its own line (rounded), so the profit ladder still reads 0–100% and
    // the floor label under a profit year's zero line reads its real share.
    private var percentageLabels: [String] {
        guard data.netRevenue > 0 else {
            return gridValues.map { _ in "—" }
        }
        return gridValues.map { value in
            let pct = (value / data.netRevenue) * 100
            // `Int()` traps on non-finite / out-of-Int-range input. Beyond ±9,999% a dash,
            // not a clamp: a gain thousands of times revenue printed "9999%" on four lines.
            guard pct.isFinite, abs(pct) <= 9_999 else { return "—" }
            return "\(Int(pct.rounded()))%"
        }
    }

    var body: some View {
        if data.hasChartableMagnitude {
            HStack(alignment: .top, spacing: 0) {
                // Left Y-axis (absolute values)
                leftYAxis
                    .frame(width: leftAxisWidth)

                // Main chart
                chartContent
                    .frame(height: chartHeight)

                // Right Y-axis (percentages)
                rightYAxis
                    .frame(width: rightAxisWidth)
            }
        } else {
            noRevenuePlaceholder
        }
    }

    // Convert a data value to a Y offset (from top of chart)
    private func yPosition(for value: Double, height: CGFloat) -> CGFloat {
        CGFloat((chartTopValue - value) / chartRange) * height
    }

    // MARK: - Empty state

    /// Nothing reaches a dollar (see `RevenueBreakdownData.hasChartableMagnitude`): no axis
    /// is invented for it.
    private var noRevenuePlaceholder: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "chart.bar.xaxis")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textMuted)
            Text("No revenue reported")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            Text("There is no revenue, cost or profit figure to chart for this year.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, minHeight: 120)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Left Y-Axis

    private var leftYAxis: some View {
        GeometryReader { geometry in
            let height = geometry.size.height
            ForEach(Array(gridValues.enumerated()), id: \.offset) { _, value in
                Text(formatLargeNumber(value))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .position(x: leftAxisWidth / 2, y: yPosition(for: value, height: height))
            }
        }
        .frame(width: leftAxisWidth, height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Right Y-Axis

    private var rightYAxis: some View {
        GeometryReader { geometry in
            let height = geometry.size.height
            // Zip instead of indexing gridValues by percentageLabels' offset —
            // they are two independently computed arrays that merely happen to
            // be the same length today, and the subscript was unchecked.
            ForEach(
                Array(zip(percentageLabels, gridValues).enumerated()),
                id: \.offset
            ) { _, pair in
                Text(pair.0)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .position(x: rightAxisWidth / 2, y: yPosition(for: pair.1, height: height))
            }
        }
        .frame(width: rightAxisWidth, height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height
            let barWidth: CGFloat = 55  // Reduced from 70 to 55

            ZStack(alignment: .topLeading) {
                // Grid lines
                gridLines(height: height)

                // Zero line whenever anything is drawn below zero — profit years included
                if hasNegativeRegion {
                    zeroLine(height: height)
                }

                // Revenue stacked bar (left side) - stands on the zero line
                revenueStackedBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.22, y: height / 2)

                // Cost waterfall bar (center) - descends FROM TOP of the revenue stack
                costWaterfallBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.50, y: height / 2)

                // Net profit/loss bar (right side)
                netProfitBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.78, y: height / 2)
            }
        }
        // Backstop only: the bounds above already contain every bar and caption. If they
        // ever stop doing so, a bar is cut at the plot edge instead of painting over the
        // header and the legend (the AVGO TestFlight overflow, in this chart's shape).
        .clipped()
    }

    // MARK: - Grid Lines

    private func gridLines(height: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(gridValues.enumerated()), id: \.offset) { _, value in
                Rectangle()
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                    .frame(height: 0.5)
                    .offset(y: yPosition(for: value, height: height))
            }
        }
        .frame(height: height)
    }

    // MARK: - Zero Line (whenever anything is drawn below zero)

    private func zeroLine(height: CGFloat) -> some View {
        let zeroY = height * (1 - zeroLinePosition)

        return Rectangle()
            .fill(AppColors.textMuted.opacity(0.8))
            .frame(height: 1)
            .offset(y: zeroY)
    }

    // MARK: - Revenue Stacked Bar

    private func revenueStackedBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let revenueBarHeight = CGFloat(data.totalRevenue / chartRange) * height
        // The stack's top, measured from the plot top. Its bottom is then the zero line
        // wherever that sits — an offset, not a spacer, so nothing in the column can push
        // it (the old spacer only lifted the stack in a LOSS year).
        let rawTopY = yPosition(for: data.totalRevenue, height: height)
        let topY = rawTopY.isFinite ? rawTopY : height

        // Calculate segment heights proportionally within the revenue bar.
        // Guard the divisor: the backend emits a single value=0 "Total Revenue" row
        // when a ticker has no segmentation, so totalRevenue can be 0 — source.value /
        // 0 is NaN/Inf, which yields a non-finite .frame(height:) ("Invalid frame
        // dimension"). Degrade to a finite, non-negative height (matches the other
        // guarded divisors in this view: chartTopValue, chartRange, percentageLabels).
        let segments: [(color: Color, height: CGFloat)] = data.revenueSources.map { source in
            let fraction = data.totalRevenue > 0 ? source.value / data.totalRevenue : 0
            let h = CGFloat(fraction) * revenueBarHeight
            return (source.color, h.isFinite ? max(0, h) : 0)
        }

        return ZStack(alignment: .top) {
            VStack(spacing: 0) {
                ForEach(0..<segments.count, id: \.self) { index in
                    Rectangle()
                        .fill(segments[index].color)
                        .frame(width: barWidth, height: segments[index].height)
                }
            }
            .clipShape(
                UnevenRoundedRectangle(
                    topLeadingRadius: 6,
                    bottomLeadingRadius: 0,
                    bottomTrailingRadius: 0,
                    topTrailingRadius: 6
                )
            )
            .offset(y: topY)
        }
        .frame(width: barWidth, height: height, alignment: .top)
    }

    // MARK: - Cost Waterfall Bar (descends from top)

    private func costWaterfallBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let pixelsPerUnit = height / chartRange

        // Calculate heights
        // A cost can legitimately be NEGATIVE (FMP reports a negative
        // incomeTaxExpense as a tax benefit). A negative or non-finite
        // `.frame(height:)` is rejected by SwiftUI, so clamp to 0 here the same
        // way the revenue segments already do.
        func barHeight(_ value: Double) -> CGFloat {
            let h = CGFloat(value) * pixelsPerUnit
            return h.isFinite ? max(h, 0) : 0
        }

        // Driven by `data.costItems` rather than three hardcoded lines, so the bar and the
        // legend cannot disagree about what the segments ARE. Two consequences:
        //   • "Interest & Other" appears once the backend sends the composition;
        //   • a CREDIT is excluded — it is income, not a cost, so it has no business in a
        //     cost bar. The `max(h, 0)` clamp above used to be the only thing hiding it,
        //     which silently made the drawn bar disagree with the totals.
        // `waterfallItems`, not `costItems`: a gross stack gets the eliminations bridge as
        // its first step, so the waterfall starts at the segment total and the costs are
        // measured from reported revenue — the level the 100% grid line marks.
        let costSegments = data.waterfallItems.filter { !$0.isCredit }

        // Top of cost bar aligns with top of revenue bar
        let rawRevenueTopY = CGFloat(chartTopValue - data.totalRevenue) * pixelsPerUnit
        let revenueTopY = rawRevenueTopY.isFinite ? max(rawRevenueTopY, 0) : 0

        // Placed by OFFSET inside a top-aligned frame: an over-full spacer stack used to be
        // centred by its frame, which slid this column's top off the revenue top it is
        // meant to line up with. The bounds guarantee it ends inside the plot.
        return ZStack(alignment: .top) {
            VStack(spacing: 0) {
                ForEach(costSegments) { item in
                    Rectangle()
                        .fill(item.chartColor)
                        .frame(width: barWidth, height: barHeight(item.value))
                }
            }
            .clipShape(
                UnevenRoundedRectangle(
                    topLeadingRadius: 6,
                    bottomLeadingRadius: data.isProfit ? 0 : 6,
                    bottomTrailingRadius: data.isProfit ? 0 : 6,
                    topTrailingRadius: 6
                )
            )
            .offset(y: revenueTopY)
        }
        .frame(width: barWidth, height: height, alignment: .top)
    }

    // MARK: - Net Profit/Loss Bar

    private func netProfitBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let pixelsPerUnit = height / chartRange
        let rawBarHeight = CGFloat(abs(data.netProfit)) * pixelsPerUnit
        let trueHeight = rawBarHeight.isFinite ? rawBarHeight : 0
        let netProfitHeight = data.netProfit == 0 ? 0 : max(trueHeight, minimumNetBarHeight)
        // Both bars stand on the ZERO LINE (the frame bottom only when nothing dips below
        // zero). A profit bar used to stand on the frame bottom even when the waterfall had
        // pushed the zero line up.
        let zeroY = height * (1 - zeroLinePosition)

        if data.isProfit {
            // Profit bar - grows UP from the zero line; caption overlaid above it
            return AnyView(
                ZStack(alignment: .top) {
                    Rectangle()
                        .fill(AppColors.bullish)
                        .frame(width: barWidth, height: netProfitHeight)
                        .clipShape(
                            UnevenRoundedRectangle(
                                topLeadingRadius: 6,
                                bottomLeadingRadius: 0,
                                bottomTrailingRadius: 0,
                                topTrailingRadius: 6
                            )
                        )
                        .overlay(alignment: .top) {
                            // Bottom of the caption 2pt above the bar's top. An overlay
                            // never sizes the column, so it cannot shift the bar.
                            Text("Net Profit")
                                .font(AppTypography.captionSmall)
                                .foregroundColor(AppColors.textMuted)
                                .fixedSize()
                                .alignmentGuide(.top) { $0[.bottom] + 2 }
                        }
                        .offset(y: zeroY - netProfitHeight)
                }
                .frame(width: barWidth, height: height, alignment: .top)
            )
        } else {
            // Loss bar - hangs DOWN from the zero line; caption overlaid below it
            return AnyView(
                ZStack(alignment: .top) {
                    Rectangle()
                        .fill(AppColors.loss)
                        .frame(width: barWidth, height: netProfitHeight)
                        .clipShape(
                            UnevenRoundedRectangle(
                                topLeadingRadius: 0,
                                bottomLeadingRadius: 6,
                                bottomTrailingRadius: 6,
                                topTrailingRadius: 0
                            )
                        )
                        .overlay(alignment: .bottom) {
                            // Top of the caption 2pt below the bar's bottom.
                            Text("-Net Loss")
                                .font(AppTypography.captionSmall)
                                .foregroundColor(AppColors.textMuted)
                                .fixedSize()
                                .alignmentGuide(.bottom) { $0[.top] - 2 }
                        }
                        .offset(y: zeroY)
                }
                .frame(width: barWidth, height: height, alignment: .top)
            )
        }
    }

    // MARK: - Helper Functions

    private func formatLargeNumber(_ number: Double) -> String {
        // Shared formatter. This copy used `%.0f` in EVERY tier, and the y-axis ticks are
        // spaced at 0.25·max — so a $2B maximum rendered `0, 0B, 1B, 2B, 2B`, i.e. two
        // pairs of duplicate labels on a chart that looked simply broken.
        CompactNumberFormat.string(number)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.xxl) {
                Text("Profitable Company (Apple)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleApple)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                Divider()
                    .overlay(AppColors.textMuted)

                Text("Loss-Making Company (Rivian)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleLossCompany)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                // Outlier shapes: every bar and caption must stay inside its frame.
                Text("Operating loss, net profit (sample)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleOperatingLossTurnedProfit)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                Text("Net income above revenue (sample)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleGainAboveRevenue)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                Text("Small net loss (sample)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleSmallLoss)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                Text("No revenue reported (sample)")
                    .foregroundColor(AppColors.textOnAccent)
                    .font(AppTypography.headingSmall)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleNoRevenue)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)
            }
            .padding()
        }
    }
}
