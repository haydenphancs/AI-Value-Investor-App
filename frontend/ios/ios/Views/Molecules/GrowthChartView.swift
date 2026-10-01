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
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    /// Trailing breathing room baked INTO the content width (not outer padding).
    /// SCROLLING needs a generous inset so the newest column — pinned at the
    /// trailing scroll anchor — isn't clipped. The STATIC (non-scrolling) chart
    /// only needs a hair of margin so the last label clears the edge; all the
    /// remaining width goes to the bars, so the chart fills the card instead of
    /// floating with a big right-hand gap.
    // Edge room is now handled by the chart's `.plotDimension(padding: edgeLabelPad)`
    // (see chartArea), so these only size the scroll CONTENT — keep them 0 so the
    // bars span the full width with no extra trailing gap.
    private let trailingInset: CGFloat = 0          // scrollable (> visibleColumnCount bars)
    private let staticTrailingInset: CGFloat = 0    // non-scrolling

    private var needsScroll: Bool { dataPoints.count > Int(visibleColumnCount) }
    
    // Computed properties for chart bounds — SIGN-AWARE so loss-maker metrics
    // (negative Net Income / FCF / Operating Profit / EPS) render downward from a
    // visible zero baseline instead of being clipped, and an all-negative series
    // doesn't produce an inverted/empty `0...negative` domain (which traps
    // chartYScale at runtime: ClosedRange requires lowerBound <= upperBound).
    private var barValues: [Double] { dataPoints.map { $0.value } }

    /// Bar value domain. Always lowerBound <= 0 <= upperBound and never empty.
    private var yDomain: ClosedRange<Double> {
        var lo = Swift.min(barValues.min() ?? 0, 0)
        var hi = Swift.max(barValues.max() ?? 1, 0)
        if hi > 0 { hi *= 1.15 }        // headroom above zero
        if lo < 0 { lo *= 1.15 }        // headroom below zero
        if lo == hi { hi = lo + 1 }     // all-zero degenerate → tiny non-empty band
        return lo...hi
    }

    // Only meaningful (non-nil) YoY / sector values feed the normalization range.
    private var yoyValues: [Double] {
        dataPoints.compactMap { $0.yoyChangePercent }
    }

    private var sectorValues: [Double] {
        dataPoints.compactMap { $0.sectorAverageYoY }
    }

    /// Robust, FLEXIBLE display range for the YoY / sector overlay lines. The
    /// printed % numbers are exact, but the LINE position uses an IQR fence so a
    /// single outlier (e.g. a sign-flip -4325% next to typical 20–30% values)
    /// pins to the edge instead of flattening every other point. There is no
    /// right-hand % axis — the line conveys RELATIVE trend, not an exact scale.
    private var yoyDisplayRange: (min: Double, max: Double) {
        let sorted = (yoyValues + sectorValues).sorted()
        guard sorted.count >= 4 else {
            let lo = sorted.first ?? -10
            let hi = sorted.last ?? 10
            let padding = Swift.max((hi - lo) * 0.2, 10)
            return (lo - padding, hi + padding)
        }
        let q1 = sorted[sorted.count / 4]
        let q3 = sorted[3 * sorted.count / 4]
        let iqr = q3 - q1
        let fence = Swift.max(iqr * 1.5, 5)
        let rangeMin = q1 - fence
        let rangeMax = q3 + fence
        let padding = Swift.max((rangeMax - rangeMin) * 0.1, 5)
        return (rangeMin - padding, rangeMax + padding)
    }

    /// Smallest vertical gap, in points, between two y-axis ticks — keeps an 11pt
    /// caption from overprinting its neighbour on an asymmetric domain.
    private let minTickSpacing: CGFloat = 24

    /// ONE tick list for the gridlines AND the y-axis labels, top → bottom.
    ///
    /// They used to come from two formulas: gridlines at thirds of EACH side of zero,
    /// labels at thirds of the WHOLE span. On a loss-maker ([-23.7B, 5B, 10B]) no label
    /// sat on any gridline and the zero baseline was unlabelled, with "-1.4B" printed
    /// just under it. Now every label is a gridline. Always 0; each domain end when it
    /// clears 0 by `minTickSpacing`; the interior thirds of each side only where they
    /// clear every tick already kept (hi = 0.5B over lo = -27B would otherwise stack
    /// three labels inside ~4pt).
    ///
    /// Every candidate is SNAPPED to the precision its label prints (`snapToLabel`)
    /// before the spacing check, so a gridline sits exactly on the value it names.
    /// Exact thirds labelled by a rounding formatter put near-break-even EPS
    /// [0.01, 0.02, 0.04] on gridlines at 0.0153 / 0.0307 / 0.046 named "0.02" /
    /// "0.03" / "0.05" — the 0.02 bar topped out ~22pt above its own "0.02" line. The
    /// two domain ends snap TOWARD zero so the outer gridlines stay inside the plot;
    /// the interior thirds snap to the nearest printable value. A candidate that
    /// snaps onto 0 or onto a kept tick fails the spacing check (distance 0) and is
    /// dropped, so labels still never stack or repeat.
    private var yTicks: [Double] {
        let hi = yDomain.upperBound, lo = yDomain.lowerBound
        let span = hi - lo
        guard span > 0, span.isFinite else { return [0] }
        let plotHeight = Double(chartHeight)
        let minGap = Double(minTickSpacing)
        var ticks: [Double] = [0]
        var candidates: [Double] = []
        // Clamped as well: `value * 100` can round UP by an ulp (3.4499999999999997 →
        // 345.0), which would put the "toward zero" end a hair outside the domain.
        if hi > 0 { candidates.append(Swift.min(snapToLabel(hi, .towardZero), hi)) }
        if lo < 0 { candidates.append(Swift.max(snapToLabel(lo, .towardZero), lo)) }
        if hi > 0 {
            candidates += [
                snapToLabel(2 * hi / 3, .toNearestOrAwayFromZero),
                snapToLabel(hi / 3, .toNearestOrAwayFromZero),
            ]
        }
        if lo < 0 {
            candidates += [
                snapToLabel(lo / 3, .toNearestOrAwayFromZero),
                snapToLabel(2 * lo / 3, .toNearestOrAwayFromZero),
            ]
        }
        // An interior third of a sub-cent side can round OUTWARD past its domain end
        // (lo = -0.0077: 2·lo/3 → -0.01): such a gridline would sit outside the plot.
        for candidate in candidates where candidate.isFinite && candidate >= lo && candidate <= hi {
            let clearsAll = ticks.allSatisfy { kept in
                abs(kept - candidate) / span * plotHeight >= minGap
            }
            if clearsAll { ticks.append(candidate) }
        }
        return ticks.sorted(by: >)
    }

    /// `value` rounded (by `rule`) to the precision `formatLargeNumber` prints it at, so
    /// the tick IS the number its label shows. Below 1,000 that is the cent (the label's
    /// own `(number * 100).rounded() / 100`); at or above it, CompactNumberFormat's unit:
    /// one decimal of K/M/B/T while the scaled value is below 10, whole units from 10.
    private func snapToLabel(_ value: Double, _ rule: FloatingPointRoundingRule) -> Double {
        guard value.isFinite else { return value }
        let magnitude = abs(value)
        if magnitude < 1_000 {
            return (value * 100).rounded(rule) / 100
        }
        let unit: Double
        if magnitude >= 1_000_000_000_000 {
            unit = 1_000_000_000_000
        } else if magnitude >= 1_000_000_000 {
            unit = 1_000_000_000
        } else if magnitude >= 1_000_000 {
            unit = 1_000_000
        } else {
            unit = 1_000
        }
        let step: Double = magnitude / unit >= 10 ? unit : unit / 10
        return (value / step).rounded(rule) * step
    }

    // Grid lines sit exactly on the labelled ticks (zero baseline always included).
    private var gridValues: [Double] { yTicks }

    /// Vertical centre of a y-axis label, in plot points from the top: the tick's own
    /// gridline, clamped so the top/bottom labels stay inside the axis column.
    private func tickLabelCenterY(_ value: Double, textHeight: CGFloat) -> CGFloat {
        let hi = yDomain.upperBound, lo = yDomain.lowerBound
        let span = hi - lo
        guard span > 0 else { return chartHeight / 2 }
        let raw = CGFloat((hi - value) / span) * chartHeight
        let half = textHeight / 2
        return Swift.min(Swift.max(raw, half), chartHeight - half)
    }

    /// Group consecutive non-nil points into segments (id increments across each
    /// nil gap) so a percentage LineMark BREAKS at "not meaningful" periods
    /// instead of bridging them with a fabricated straight segment.
    private func percentSegments(
        _ valueFor: @escaping (GrowthDataPoint) -> Double?
    ) -> [(index: Int, value: Double, seg: Int)] {
        var out: [(index: Int, value: Double, seg: Int)] = []
        var seg = 0
        var prevWasNil = true
        for (i, p) in dataPoints.enumerated() {
            guard let v = valueFor(p) else { prevWasNil = true; continue }
            if prevWasNil { seg += 1; prevWasNil = false }
            out.append((index: i, value: v, seg: seg))
        }
        return out
    }

    private var yoySegments: [(index: Int, value: Double, seg: Int)] {
        percentSegments { $0.yoyChangePercent }
    }

    private var sectorSegments: [(index: Int, value: Double, seg: Int)] {
        percentSegments { $0.sectorAverageYoY }
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

    /// Height of the whole component (plot + x-axis row + value/YoY/sector rows), shared
    /// by the chart and its empty state so switching chips never makes the card jump.
    private var componentHeight: CGFloat {
        chartHeight + 20 + AppSpacing.md + (20 + AppSpacing.sm) * 3
    }

    var body: some View {
        if dataPoints.isEmpty {
            // An empty series (a failed FMP leg, or a metric this company never reports)
            // used to draw a fabricated 0–1.2 axis over an empty plot with no message.
            emptyState
        } else {
            chartBody
        }
    }

    private var emptyState: some View {
        Text("No data for this period")
            .font(AppTypography.bodySmall)
            .foregroundColor(AppColors.textMuted)
            .frame(maxWidth: .infinity)
            .frame(height: componentHeight)
    }

    private var chartBody: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: Y-axis labels (fixed, never scrolls). Sizes to its OWN
            // content width (not a fixed column) so short labels like EPS's "6.7"
            // don't leave a big empty gap before the chart — the plot starts right
            // after the numbers. Wider labels (e.g. "474B") just push it out.
            VStack(alignment: .leading, spacing: 0) {
                barYAxisLabels

                // Spacer matching x-axis labels + value/YoY/sector label rows
                Spacer()
                    .frame(height: 20 + AppSpacing.md + (20 + AppSpacing.sm) * 3)
            }
            .fixedSize(horizontal: true, vertical: false)

            // Right column: scrollable chart area
            GeometryReader { geometry in
                let visibleWidth = Swift.max(geometry.size.width, 1)
                let count = dataPoints.count
                // The chart is framed to `barsWidth` and the manual label rows to
                // `contentWidth`; both position columns via the SAME `xCenter` grid
                // so they stay aligned. Edge room for the end labels comes from the
                // chart's `.plotDimension(padding:)`, not from these insets.
                let barsWidth = needsScroll
                    ? CGFloat(count) * (visibleWidth / visibleColumnCount)
                    : Swift.max(visibleWidth - staticTrailingInset, 1)
                let contentWidth = needsScroll ? barsWidth + trailingInset : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(alignment: .leading, spacing: 0) {
                        chartArea(barsWidth: barsWidth)
                            .frame(width: barsWidth, height: chartHeight)

                        xAxisLabels(barsWidth: barsWidth, totalWidth: contentWidth)
                            .padding(.top, AppSpacing.md)

                        valueLabels(barsWidth: barsWidth, totalWidth: contentWidth)
                            .padding(.top, AppSpacing.sm)

                        yoyPercentageLabels(barsWidth: barsWidth, totalWidth: contentWidth)
                            .padding(.top, AppSpacing.sm)

                        sectorAverageLabels(barsWidth: barsWidth, totalWidth: contentWidth)
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: contentWidth, alignment: .leading)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: componentHeight + (needsScroll ? AppSpacing.md : 0))
        }
    }

    // MARK: - X positioning (edge-to-edge)

    /// Slim bar width (user wants small columns), sized off the per-column slot.
    private func barWidth(_ barsWidth: CGFloat) -> CGFloat {
        let slot = barsWidth / CGFloat(Swift.max(dataPoints.count, 1))
        return Swift.min(Swift.max(slot * 0.6, 2), 28)
    }

    /// Room reserved at each plot edge for the widest centered edge LABEL
    /// ("-47.0%", "Q1 '24"…). In POINTS — label width is font-fixed, not a
    /// fraction of the chart — so the edge column never clips at any chart width.
    private let edgeLabelPad: CGFloat = 22

    /// Plain index domain 0…N-1. The edge spacing is set by the chart's
    /// `.plotDimension(padding:)` (NOT baked into the domain), so the bars span
    /// the full plot minus `edgeLabelPad` at each end.
    private func xDomain() -> ClosedRange<Double> {
        let n = dataPoints.count
        guard n > 1 else { return -0.5 ... 0.5 }          // lone bar: centered
        return 0.0 ... Double(n - 1)
    }

    /// Pixel center of column `index` — mirrors the chart's scale (domain 0…N-1
    /// mapped into the plot with `edgeLabelPad` padding at each end) so the manual
    /// label rows sit dead-center over their bars/points.
    private func xCenter(_ index: Int, barsWidth: CGFloat) -> CGFloat {
        let n = dataPoints.count
        guard n > 1 else { return barsWidth / 2 }
        let usable = Swift.max(barsWidth - 2 * edgeLabelPad, 1)
        return edgeLabelPad + CGFloat(index) / CGFloat(n - 1) * usable
    }

    // MARK: - Chart Area

    private func chartArea(barsWidth: CGFloat) -> some View {
        // Slim bars at integer x indices, spread EDGE-TO-EDGE by `xDomain` (no
        // categorical half-slot of dead space after the last column). The manual
        // label rows use the SAME `xCenter` grid → bars + labels stay aligned.
        let barW = barWidth(barsWidth)
        return Chart {
            // Horizontal grid lines (behind everything)
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // Bar marks for absolute values — negative bars render red and
            // downward from the zero baseline (sign-aware yDomain).
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                BarMark(
                    x: .value("i", Double(index)),
                    y: .value("Value", dataPoint.value),
                    width: .fixed(barW)
                )
                .foregroundStyle(dataPoint.value < 0 ? AppColors.bearish : AppColors.growthBarBlue)
                .cornerRadius(4)
            }

            // YoY line — one segment per contiguous non-nil run, so the line
            // BREAKS at "not meaningful" (nil) periods instead of bridging them.
            ForEach(Array(yoySegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("i", Double(item.index)),
                    y: .value("YoY", normalizeYoY(item.value)),
                    series: .value("Series", "YoY-\(item.seg)")
                )
                .foregroundStyle(AppColors.growthYoYYellow)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.linear)
            }

            // YoY points (only meaningful periods)
            ForEach(Array(yoySegments.enumerated()), id: \.offset) { _, item in
                PointMark(
                    x: .value("i", Double(item.index)),
                    y: .value("YoY", normalizeYoY(item.value))
                )
                .foregroundStyle(AppColors.growthYoYYellow)
                .symbolSize(50)
            }

            // Sector average line — dashed, also broken at periods with no benchmark.
            ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("i", Double(item.index)),
                    y: .value("Sector", normalizeYoY(item.value)),
                    series: .value("Series", "Sector-\(item.seg)")
                )
                .foregroundStyle(AppColors.growthSectorGray)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 4]))
                .interpolationMethod(.linear)
            }

            // Sector average points (only periods with a benchmark)
            ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                PointMark(
                    x: .value("i", Double(item.index)),
                    y: .value("Sector", normalizeYoY(item.value))
                )
                .foregroundStyle(AppColors.growthSectorGray)
                .symbolSize(35)
            }

        }
        // Index domain 0…N-1. `range: .plotDimension(padding:)` is LOAD-BEARING:
        // without it Swift Charts adds a large default plot inset that clusters the
        // bars to the left and leaves a big gap on the right. Pinning the inset to
        // `edgeLabelPad` makes the bars span nearly the full width (only the end
        // labels' room reserved). Same fix as SmartMoneyFlowChart.
        .chartXScale(domain: xDomain(), range: .plotDimension(padding: edgeLabelPad))
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: yDomain)
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
    }

    // MARK: - Y-Axis Labels

    private var barYAxisLabels: some View {
        // One label per `yTicks` entry, each centred on its own gridline (positioned by
        // an alignment guide, not by Spacers between four fixed labels). The clear
        // anchor pins the stack to the plot's 0…chartHeight, and the fixed-size labels
        // give the column its intrinsic width — the axis still hugs the card's edge.
        let ticks: [Double] = yTicks
        return ZStack(alignment: .topLeading) {
            Color.clear
                .frame(width: 0, height: chartHeight)
            ForEach(ticks, id: \.self) { tick in
                Text(tick == 0 ? "0" : formatLargeNumber(tick))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .alignmentGuide(VerticalAlignment.top) { d in
                        d.height / 2 - tickLabelCenterY(tick, textHeight: d.height)
                    }
            }
        }
        .frame(height: chartHeight, alignment: .topLeading)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Manual label rows
    //
    // Each row positions Text at the SAME column centers (`xCenter`) the chart's
    // bars/points use, then frames to `totalWidth` (= barsWidth + trailingInset
    // when scrolling) with .leading alignment — so every label sits dead-center
    // over its bar, and the newest column's label renders INTO the trailing inset
    // instead of clipping at the scroll edge.

    private func xAxisLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(dataPoint.period)
                    .font(.system(size: labelFontSize, weight: .regular))
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: xCenter(index, barsWidth: barsWidth), y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func valueLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(formatLargeNumber(dataPoint.value))
                    .font(.system(size: labelFontSize, weight: .semibold))
                    // A readable number is TEXT (4.5), not a chart mark (3.0). The series
                    // token `growthBarBlue` is certified at 3:1 and measured 3.68:1 on a
                    // light card / 3.22:1 nested. Colour stays with the BAR; the label
                    // takes the series' text-safe sibling. Same swap in the other four
                    // chart files — see the palette rule that `*Graphic` tokens must not
                    // escape the chart layer.
                    .foregroundColor(AppColors.primaryBlue)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: xCenter(index, barsWidth: barsWidth), y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func yoyPercentageLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                // nil YoY (undefined base) → muted "—"; otherwise the exact %.
                Text(dataPoint.yoyChangePercent.map { fmtYoY($0) } ?? "—")
                    .font(.system(size: yoyFontSize, weight: .semibold))
                    .foregroundColor(
                        dataPoint.yoyChangePercent.map { $0 >= 0 ? AppColors.bullish : AppColors.bearish }
                            ?? AppColors.textMuted
                    )
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: xCenter(index, barsWidth: barsWidth), y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func sectorAverageLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                // nil sector value (no benchmark for this period) → muted "—".
                Text(dataPoint.sectorAverageYoY.map { fmtYoY($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .regular))
                    // Text-safe sibling of `growthSectorGray` (3.78:1 light card). Dark is
                    // the same #9CA3AF, so this is a light-mode correction only.
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: xCenter(index, barsWidth: barsWidth), y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    // MARK: - Helper Functions

    /// Map a % value to a RELATIVE position in the plot using the robust
    /// `yoyDisplayRange` (IQR fence), so outliers clamp to the band edges instead
    /// of flattening the rest. This is a trend position, not an exact scale —
    /// the precise % is shown in the numeric label row, not read off an axis.
    private func normalizeYoY(_ yoyPercent: Double) -> Double {
        let range = yoyDisplayRange
        let span = range.max - range.min
        let lo = yDomain.lowerBound, hi = yDomain.upperBound
        guard span > 0 else { return (lo + hi) / 2 }
        let normalized = (yoyPercent - range.min) / span
        let clampedN = Swift.min(Swift.max(normalized, 0.0), 1.0)
        // Map into the 10%..85% band of the plot height (leaves room top/bottom).
        let targetMin = lo + (hi - lo) * 0.10
        let targetMax = lo + (hi - lo) * 0.85
        return targetMin + clampedN * (targetMax - targetMin)
    }

    /// Compact, CORRECT % — drops decimals once the magnitude is large (a
    /// sign-flip YoY can be in the thousands of %; "-4325%" reads cleaner than
    /// "-4325.0%" and never gets truncated).
    private func fmtYoY(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.1f%%", v)
    }

    private func formatLargeNumber(_ number: Double) -> String {
        guard number.isFinite else { return "—" }
        // Below 1,000 the value is a PER-SHARE figure (EPS) — every statement total is in
        // dollars and far larger. CompactNumberFormat keeps one decimal below 10 and none
        // at 10+, so near-break-even EPS (0.02 / -0.03) printed "0" / "-0" over a "+300%"
        // YoY, and 10.40 / 10.60 printed "10" / "11". Cents, rounded first so a tiny
        // negative cannot print "-0.00"; an exact zero stays "0".
        if number == 0 { return "0" }
        if abs(number) < 1_000 {
            let cents = (number * 100).rounded() / 100
            return String(format: "%.2f", cents == 0 ? 0 : cents)
        }
        // Shared formatter. This copy printed whole B/M/K directly above a YoY row that
        // keeps one decimal, so the two rows of the SAME chart contradicted each other:
        // a bar labelled "2B" sat over "+12.3%" computed from 2.4B.
        return CompactNumberFormat.string(number)
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

#Preview("Loss-maker, near-zero EPS, empty") {
    ScrollView {
        VStack(spacing: AppSpacing.xl) {
            // Mixed sign: every y label must sit on a gridline, 0 labelled.
            GrowthChartView(dataPoints: [
                GrowthDataPoint(period: "2023", value: -23_700_000_000, yoyChangePercent: nil, sectorAverageYoY: 4.0),
                GrowthDataPoint(period: "2024", value: 5_000_000_000, yoyChangePercent: nil, sectorAverageYoY: 6.0),
                GrowthDataPoint(period: "2025", value: 10_000_000_000, yoyChangePercent: 100.0, sectorAverageYoY: 5.0)
            ])
            // Near-break-even EPS: cents, never "0" / "-0".
            GrowthChartView(dataPoints: [
                GrowthDataPoint(period: "Q1 '25", value: 0.02, yoyChangePercent: nil, sectorAverageYoY: nil),
                GrowthDataPoint(period: "Q2 '25", value: -0.03, yoyChangePercent: nil, sectorAverageYoY: nil),
                GrowthDataPoint(period: "Q3 '25", value: 0.04, yoyChangePercent: 300.0, sectorAverageYoY: nil)
            ])
            // Empty series: a message, not a fabricated axis.
            GrowthChartView(dataPoints: [])
        }
        .padding()
    }
    .background(AppColors.background)
}
