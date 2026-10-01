//
//  EarningsSurpriseBarChart.swift
//  ios
//
//  Molecule: Vertical bar chart displaying quarterly EPS surprise percentages
//  Background chart for 3Y view only - shows historical beat/miss trends
//

import SwiftUI

/// A pair of symmetric y-bounds, in percent.
typealias EarningsSurpriseDomain = (min: Double, max: Double)

struct EarningsSurpriseBarChart: View {
    let quarters: [EarningsQuarterData]
    /// The main chart's data type. Only the y-axis gutter depends on it, and that gutter
    /// MUST equal EarningsChartView's or every bar sits left of its dot.
    var dataType: EarningsDataType = .eps

    /// The surprise a quarter's bar is drawn from, or nil when it gets no bar. A
    /// non-finite value cannot be placed, and a quarter with no analyst consensus has no
    /// surprise to show (its dot says "Reported"). The ONE gate: the domain, the bars and
    /// the off-scale label placement all read it, so they agree on which columns hold a bar.
    static func barSurprise(_ quarter: EarningsQuarterData) -> Double? {
        guard quarter.result != .noEstimate,
              let surprise = quarter.surprisePercent,
              surprise.isFinite else { return nil }
        return surprise
    }

    /// The surprises that get a bar.
    private var plottableSurprises: [Double] {
        quarters.compactMap { Self.barSurprise($0) }
    }

    // Calculate Y-axis range based on surprise percentages
    // Range is symmetric to ensure 0% line is centered
    private var surpriseRange: EarningsSurpriseDomain {
        Self.surpriseDomain(plottableSurprises)
    }

    /// Symmetric about 0 (the axis prints only max / 0 / min, so the 0% line must sit in
    /// the middle).
    ///
    /// One outlier must not set the scale for every ordinary quarter: AVGO's 11 quarters
    /// within ±4% next to one −88% quarter, or a +45,000% surprise on a near-zero
    /// consensus, would otherwise flatten every normal bar into a sliver. So the domain
    /// stops at a robust fence, and any bar beyond it is CLAMPED to the edge and marked
    /// (see `barGeometry` and the off-scale chevron + true-value label in `body`).
    ///
    /// * 4+ values: Tukey's far-out fence on |surprise| (ChartDomain.robust), used only
    ///   when the largest value is more than twice the fence — a value just past it is
    ///   drawn in full rather than pinned a hair short of its real length.
    /// * 2-3 values: the largest is capped at 1.5x the second when it is 10x larger.
    /// * Never narrower than ±1%: a company that met consensus exactly every quarter
    ///   (all 0, or all |x| < 1) collapsed the range to (-0, 0), which stacked all three
    ///   axis labels at the same y reading "0%".
    static func surpriseDomain(_ surprises: [Double]) -> EarningsSurpriseDomain {
        let magnitudes = surprises.filter { $0.isFinite }.map { abs($0) }.sorted()
        guard let absMax = magnitudes.last else {
            return (min: -10, max: 10)
        }
        var cap = absMax
        if magnitudes.count >= 4 {
            let fence = ChartDomain.robust(magnitudes, includeZero: true, headroomFraction: 0).upperBound
            if absMax > fence * 2 {
                cap = fence
            }
        } else if magnitudes.count >= 2 {
            let secondLargest = magnitudes[magnitudes.count - 2]
            if secondLargest > 0, absMax > secondLargest * 10 {
                cap = secondLargest * 1.5
            }
        }
        let rounded = max(ceil(min(cap, absMax)), 1)
        return (min: -rounded, max: rounded)
    }

    /// Height from the bottom of the plot (y grows UP here), with a 7.5% inset at each end.
    /// The insets are where the off-scale chevrons are drawn.
    static func normalizedY(_ value: Double, height: CGFloat, domain: EarningsSurpriseDomain) -> CGFloat {
        let span = max(domain.max - domain.min, 0.01)
        let normalized = (value - domain.min) / span
        return CGFloat(normalized) * height * 0.85 + height * 0.075
    }

    /// One bar in VIEW coordinates (y grows down). The length comes from the surprise
    /// CLAMPED into the domain, never the raw value: the domain used to be capped for an
    /// outlier while the bar kept its true length, so AVGO's −88% quarter drew a 624pt bar
    /// out of a 100pt chart, through the legend, the Next Earnings card and the chat chips
    /// below (TestFlight 1.0(9), 2026-09-23). `isOffScale` is the cue to mark it.
    static func barGeometry(
        _ surprise: Double,
        height: CGFloat,
        domain: EarningsSurpriseDomain
    ) -> (centerY: CGFloat, height: CGFloat, isOffScale: Bool) {
        let plotted = min(max(surprise, domain.min), domain.max)
        let zeroY = normalizedY(0, height: height, domain: domain)
        let valueY = normalizedY(plotted, height: height, domain: domain)
        let barHeight = abs(valueY - zeroY)
        let centerY = height - (zeroY + valueY) / 2
        return (centerY: centerY, height: barHeight, isOffScale: plotted != surprise)
    }

    /// "+10.3%" / "-88.1%" / "+250%" / "+45k%" — the TRUE value of a clamped bar, by the
    /// 1Y row's own rules (`EarningsQuarterData.surpriseText`). It used to be whole-percent
    /// `CompactNumberFormat.percentString`, which printed a clamped 10.3% outlier as "+10%"
    /// (the axis maximum it runs past) and, as the VoiceOver text, read a −0.4% miss as "0%".
    static func signedPercent(_ surprise: Double) -> String {
        EarningsQuarterData.surpriseText(surprise, isMatch: surprise == 0)
    }

    // MARK: - Off-scale label placement

    /// Where one off-scale bar's true-value label goes: `row` 0 sits next to the 0% line,
    /// row 1 one line further from it; `width` is its frame.
    struct OffScaleLabelSlot: Equatable {
        let row: Int
        let width: CGFloat
    }

    /// One label row. The label's frame is exactly this tall: at a larger Dynamic Type size
    /// the text is offered only this height and scales down (`minimumScaleFactor`) instead
    /// of spilling into the next row.
    static let labelRowHeight: CGFloat = 14
    /// How far a label may spill past the plot's left and right edges: the y-axis gutter's
    /// empty trailing padding (`AppSpacing.sm`) on the left, the card's padding on the
    /// right. The plot's clip bleeds by exactly this much horizontally (`PlotBleedClip`).
    static let labelBleed: CGFloat = AppSpacing.sm

    /// Centre of a label on `row`, measured from the 0% line AWAY from its bar: the frame's
    /// near edge sits 2pt off the dashed line, row 1 one row (+1pt) further.
    static func labelOffset(row: Int) -> CGFloat {
        let half: CGFloat = labelRowHeight / 2
        let step: CGFloat = labelRowHeight + 1
        return 2 + half + CGFloat(row) * step
    }

    /// The labels of off-scale bars, keyed by column. Each label is centred on its own
    /// column, on the side of the 0% line its bar leaves empty. Two defects this replaces:
    ///  * two ADJACENT clamped bars in the same direction (two near-break-even quarters
    ///    with tiny consensus: +900%, +500%) put both labels on one line, 1.4 columns wide
    ///    and one column apart, so "+900%" ran into "+500%" — the only place either true
    ///    value appears in 3Y. A run of them now alternates rows 0 / 1;
    ///  * the first and last labels were clamped inward to stay inside the plot, which
    ///    pushed column 0's label over column 1's bar. Labels are now always centred (the
    ///    clip bleeds by `labelBleed` instead), and a label with a neighbouring bar on its
    ///    side of the 0% line is narrowed to stop short of that bar.
    ///
    /// `surprises` is one entry per column (`barSurprise`, nil = no bar).
    static func offScaleLabelSlots(
        _ surprises: [Double?],
        domain: EarningsSurpriseDomain,
        stepX: CGFloat
    ) -> [Int: OffScaleLabelSlot] {
        // Wide enough for "+10.3%" at the 0.7 minimum scale, but never wider than its
        // column plus the bleed on both sides (an edge label must not be cut).
        let fullWidth: CGFloat = min(max(stepX * 1.4, 28), stepX + 2 * labelBleed)
        // A neighbouring bar's inner edge is 0.75 columns from this column's centre.
        let clearOfNeighbourBars: CGFloat = max(stepX * 1.5 - 1, 0)
        var slots: [Int: OffScaleLabelSlot] = [:]
        for index in surprises.indices {
            guard let surprise = surprises[index] else { continue }
            let plotted: Double = min(max(surprise, domain.min), domain.max)
            guard plotted != surprise else { continue }
            let pointsUp: Bool = surprise > 0

            // A neighbour whose bar sits on this label's side of the 0% line.
            var crowded: Bool = false
            for neighbour in [index - 1, index + 1] where surprises.indices.contains(neighbour) {
                if let other = surprises[neighbour], other != 0, (other > 0) != pointsUp {
                    crowded = true
                }
            }
            let width: CGFloat = crowded ? min(fullWidth, clearOfNeighbourBars) : fullWidth

            // Alternate rows along a run of same-direction off-scale neighbours.
            var row: Int = 0
            if let previous = slots[index - 1],
               let previousSurprise = surprises[index - 1],
               (previousSurprise > 0) == pointsUp,
               previous.row == 0 {
                row = 1
            }
            slots[index] = OffScaleLabelSlot(row: row, width: width)
        }
        return slots
    }

    private var chartHeight: CGFloat { 100 }
    private var barWidthRatio: CGFloat { 0.5 } // Bar takes 50% of available space per quarter

    var body: some View {
        // Nothing to plot → no chart. The old fallback domain printed an invented
        // "10% / 0% / -10%" axis over an empty strip.
        if !plottableSurprises.isEmpty {
            chart(domain: surpriseRange)
        }
    }

    private func chart(domain: EarningsSurpriseDomain) -> some View {
        HStack(alignment: .top, spacing: 0) {
            // Left spacer to align with main chart (matches Y-axis width)
            yAxisLabels(domain: domain)
                .frame(width: EarningsChartLayout.yAxisWidth(for: dataType))

            // Chart area with manual bar positioning
            GeometryReader { geometry in
                let width: CGFloat = geometry.size.width
                let height: CGFloat = geometry.size.height
                let quarterCount: Int = max(quarters.count, 1)
                let stepX: CGFloat = width / CGFloat(quarterCount)
                let barWidth: CGFloat = stepX * barWidthRatio
                // View-space y of the 0% line.
                let zeroLineY: CGFloat = height - Self.normalizedY(0, height: height, domain: domain)
                let columns: [Double?] = quarters.map { Self.barSurprise($0) }
                let labelSlots: [Int: OffScaleLabelSlot] = Self.offScaleLabelSlots(
                    columns,
                    domain: domain,
                    stepX: stepX
                )

                ZStack {
                    // Zero line (horizontal line at 0%)
                    Path { path in
                        path.move(to: CGPoint(x: 0, y: zeroLineY))
                        path.addLine(to: CGPoint(x: width, y: zeroLineY))
                    }
                    .stroke(style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
                    .foregroundColor(AppColors.textMuted.opacity(0.6))

                    // Surprise bars
                    ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                        if let surprise = Self.barSurprise(quarter) {
                            let x: CGFloat = CGFloat(index) * stepX + stepX / 2
                            let bar = Self.barGeometry(surprise, height: height, domain: domain)
                            let tint: Color = surprise >= 0 ? AppColors.bullish : AppColors.bearish
                            let scaleNote: String = bar.isOffScale ? ", beyond the chart scale" : ""
                            // The 1Y row's caption ("+0.3%", "<0.1%", "0%" only for a real
                            // match) plus the outcome word, so a listener hears exactly what
                            // a sighted user reads and sees.
                            let valueText: String = quarter.formattedSurprise ?? Self.signedPercent(surprise)
                            let outcome: String = quarter.spokenOutcome.isEmpty ? "" : " \(quarter.spokenOutcome),"
                            let spoken: String = "\(quarter.quarter)\(outcome) surprise \(valueText)\(scaleNote)"

                            RoundedRectangle(cornerRadius: 3)
                                .fill(tint)
                                .frame(width: barWidth, height: bar.height)
                                .accessibilityElement()
                                .accessibilityLabel(Text(spoken))
                                .position(x: x, y: bar.centerY)

                            if bar.isOffScale {
                                offScaleMarker(
                                    surprise: surprise,
                                    x: x,
                                    barWidth: barWidth,
                                    height: height,
                                    zeroLineY: zeroLineY,
                                    slot: labelSlots[index],
                                    tint: tint
                                )
                            }
                        }
                    }
                }
            }
            .frame(height: chartHeight)
            // Backstop only: with the clamp above nothing should reach the top or bottom
            // edge. A clip ALONE would hide an outlier silently, which is why the marker is
            // drawn too. Exact vertically; `labelBleed` wider horizontally so an edge
            // column's centred value label is not cut.
            .clipShape(PlotBleedClip(horizontalBleed: Self.labelBleed))
        }
    }

    // MARK: - Helper Views

    /// A clamped bar's honest annotation:
    ///  * a chevron in the 7.5% inset beyond the pinned end — "this bar continues";
    ///  * its TRUE value just across the 0% line, centred in the column the bar leaves
    ///    empty, on the row and at the width `offScaleLabelSlots` chose so it clears its
    ///    neighbours' labels and bars. In 3Y there is no other caption (EarningsSurpriseRow
    ///    renders only in 1Y), so without this label a clamped −88% bar would read as the
    ///    axis' −8%.
    @ViewBuilder
    private func offScaleMarker(
        surprise: Double,
        x: CGFloat,
        barWidth: CGFloat,
        height: CGFloat,
        zeroLineY: CGFloat,
        slot: OffScaleLabelSlot?,
        tint: Color
    ) -> some View {
        let pointsUp: Bool = surprise > 0
        let inset: CGFloat = height * 0.075
        let chevronHeight: CGFloat = max(min(inset - 2, 5), 2)
        let chevronY: CGFloat = pointsUp ? inset / 2 : height - inset / 2
        // `slot` is computed with the same clamp test as `barGeometry`, so it is present
        // for every off-scale bar; the fallback only keeps the true value on screen.
        let row: Int = slot?.row ?? 0
        let labelWidth: CGFloat = slot?.width ?? 28
        let labelOffset: CGFloat = Self.labelOffset(row: row)
        let labelY: CGFloat = pointsUp ? zeroLineY + labelOffset : zeroLineY - labelOffset

        OffScaleChevron(pointsUp: pointsUp)
            .stroke(tint, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
            .frame(width: max(barWidth * 0.8, 6), height: chevronHeight)
            .position(x: x, y: chevronY)
            .accessibilityHidden(true)

        Text(Self.signedPercent(surprise))
            .font(AppTypography.caption)
            .fontWeight(.semibold)
            .foregroundColor(tint)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .frame(width: labelWidth, height: Self.labelRowHeight)
            .position(x: x, y: labelY)
            .accessibilityHidden(true)
    }

    private func yAxisLabels(domain: EarningsSurpriseDomain) -> some View {
        GeometryReader { geometry in
            let height = geometry.size.height

            // Calculate Y positions using the same normalization as the chart
            let maxY = Self.normalizedY(domain.max, height: height, domain: domain)
            let zeroY = Self.normalizedY(0, height: height, domain: domain)
            let minY = Self.normalizedY(domain.min, height: height, domain: domain)

            ZStack(alignment: .trailing) {
                // Max label at top
                Text(formatYValue(domain.max))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .position(x: geometry.size.width / 2, y: height - maxY)

                // Zero label at calculated position
                Text("0%")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .position(x: geometry.size.width / 2, y: height - zeroY)

                // Min label at bottom
                Text(formatYValue(domain.min))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .position(x: geometry.size.width / 2, y: height - minY)
            }
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.sm)
    }

    // MARK: - Helper Functions

    private func formatYValue(_ value: Double) -> String {
        // `Int(value)` TRAPS on a non-finite or out-of-Int-range Double. A surprise
        // percent is unbounded off the wire (a near-zero consensus estimate produces an
        // astronomically large one), so this used to CLAMP to ±9,999 — but only the
        // CAPTION. The plot domain below was left unclamped, so a +45,000% surprise drew
        // a full-height bar under an axis label reading "9999%": the number and the
        // geometry disagreed, and the label was simply false.
        //
        // Format compactly instead of clamping ("45k%"), so an extreme value stays
        // honest AND still fits the 40pt axis gutter.
        CompactNumberFormat.percentString(value)
    }
}

/// The plot's clip: the exact frame vertically (the backstop that keeps every bar inside
/// its 100pt band), `horizontalBleed` wider on each side for the edge columns' labels.
private struct PlotBleedClip: Shape {
    let horizontalBleed: CGFloat

    func path(in rect: CGRect) -> Path {
        Path(rect.insetBy(dx: -horizontalBleed, dy: 0))
    }
}

/// An open chevron pointing toward the clamped end of an off-scale bar.
private struct OffScaleChevron: Shape {
    let pointsUp: Bool

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let tipY = pointsUp ? rect.minY : rect.maxY
        let baseY = pointsUp ? rect.maxY : rect.minY
        path.move(to: CGPoint(x: rect.minX, y: baseY))
        path.addLine(to: CGPoint(x: rect.midX, y: tipY))
        path.addLine(to: CGPoint(x: rect.maxX, y: baseY))
        return path
    }
}


// MARK: - Preview

#Preview("1Y View - Limited Data") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            // 1Y view with last 6 quarters
            EarningsSurpriseBarChart(
                quarters: Array(EarningsData.sampleData.epsQuarters.suffix(6))
            )
            .padding(AppSpacing.lg)
        }
    }
}

#Preview("3Y View - Full Data") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                // 3Y view with all historical quarters
                EarningsSurpriseBarChart(
                    quarters: EarningsData.sampleData.epsQuarters
                )
                .padding(AppSpacing.lg)

                // Sample with more extreme values
                EarningsSurpriseBarChart(
                    quarters: [
                        EarningsQuarterData(quarter: "Q1 '22", actualValue: 0.45, estimateValue: 0.42, surprisePercent: 7.1),
                        EarningsQuarterData(quarter: "Q2 '22", actualValue: 0.52, estimateValue: 0.50, surprisePercent: 15.5),
                        EarningsQuarterData(quarter: "Q3 '22", actualValue: 0.48, estimateValue: 0.52, surprisePercent: -7.7),
                        EarningsQuarterData(quarter: "Q4 '22", actualValue: 0.55, estimateValue: 0.55, surprisePercent: 0),
                        EarningsQuarterData(quarter: "Q1 '23", actualValue: 0.58, estimateValue: 0.55, surprisePercent: 5.5),
                        EarningsQuarterData(quarter: "Q2 '23", actualValue: 0.62, estimateValue: 0.60, surprisePercent: -12.3),
                        EarningsQuarterData(quarter: "Q3 '23", actualValue: 0.55, estimateValue: 0.58, surprisePercent: 8.2),
                        EarningsQuarterData(quarter: "Q4 '23", actualValue: 0.68, estimateValue: 0.65, surprisePercent: 18.6),
                    ]
                )
                .padding(AppSpacing.lg)
            }
        }
    }
}

#Preview("3Y Revenue - One Outlier") {
    // The AVGO shape from the TestFlight report: eleven quarters within ±4% and one at
    // −88%. The outlier bar must stop at the plot's bottom edge with a chevron and read
    // "-88%"; nothing may draw below the 100pt chart.
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            EarningsSurpriseBarChart(
                quarters: [
                    EarningsQuarterData(quarter: "Q3 '23", actualValue: 9.30e9, estimateValue: 9.27e9, surprisePercent: 0.3),
                    EarningsQuarterData(quarter: "Q4 '23", actualValue: 9.29e9, estimateValue: 9.27e9, surprisePercent: 0.2),
                    EarningsQuarterData(quarter: "Q1 '24", actualValue: 11.96e9, estimateValue: 11.72e9, surprisePercent: 2.1),
                    EarningsQuarterData(quarter: "Q2 '24", actualValue: 12.49e9, estimateValue: 12.01e9, surprisePercent: 4.0),
                    EarningsQuarterData(quarter: "Q3 '24", actualValue: 13.07e9, estimateValue: 13.00e9, surprisePercent: 0.5),
                    EarningsQuarterData(quarter: "Q4 '24", actualValue: 14.05e9, estimateValue: 13.79e9, surprisePercent: 1.9),
                    EarningsQuarterData(quarter: "Q1 '25", actualValue: 14.92e9, estimateValue: 14.61e9, surprisePercent: 2.1),
                    EarningsQuarterData(quarter: "Q2 '25", actualValue: 15.00e9, estimateValue: 14.80e9, surprisePercent: 1.4),
                    EarningsQuarterData(quarter: "Q3 '25", actualValue: 15.95e9, estimateValue: 15.83e9, surprisePercent: 0.8),
                    EarningsQuarterData(quarter: "Q4 '25", actualValue: 18.02e9, estimateValue: 17.49e9, surprisePercent: 3.0),
                    EarningsQuarterData(quarter: "Q1 '26", actualValue: 19.31e9, estimateValue: 19.13e9, surprisePercent: 0.9),
                    EarningsQuarterData(quarter: "Q2 '26", actualValue: 2.61e9, estimateValue: 21.80e9, surprisePercent: -88.0),
                    EarningsQuarterData(quarter: "Q3 '26", actualValue: nil, estimateValue: 24.10e9, surprisePercent: nil),
                    EarningsQuarterData(quarter: "Q4 '26", actualValue: nil, estimateValue: 25.00e9, surprisePercent: nil),
                ],
                dataType: .revenue
            )
            .padding(AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.large)
            .padding(AppSpacing.lg)

            Text("Nothing below this line")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }
}
