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
    private let yAxisWidth: CGFloat = 45
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    /// Empty space kept to the RIGHT of the last bar (BAKED INTO the content
    /// width, not outer padding) so the newest column's centered bar + label
    /// have room and aren't clipped at the trailing scroll anchor.
    private let trailingInset: CGFloat = 40

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

    // Grid lines: the zero baseline plus interior lines across the (sign-aware)
    // domain, so a chart with negative bars still shows a visible 0 line.
    private var gridValues: [Double] {
        let hi = yDomain.upperBound, lo = yDomain.lowerBound
        var vals: [Double] = [0]
        if hi > 0 { vals += [hi / 3, 2 * hi / 3] }
        if lo < 0 { vals += [lo / 3, 2 * lo / 3] }
        return vals
    }

    /// Group consecutive non-nil points into segments (id increments across each
    /// nil gap) so a percentage LineMark BREAKS at "not meaningful" periods
    /// instead of bridging them with a fabricated straight segment.
    private func percentSegments(
        _ valueFor: @escaping (GrowthDataPoint) -> Double?
    ) -> [(point: GrowthDataPoint, value: Double, seg: Int)] {
        var out: [(point: GrowthDataPoint, value: Double, seg: Int)] = []
        var seg = 0
        var prevWasNil = true
        for p in dataPoints {
            guard let v = valueFor(p) else { prevWasNil = true; continue }
            if prevWasNil { seg += 1; prevWasNil = false }
            out.append((point: p, value: v, seg: seg))
        }
        return out
    }

    private var yoySegments: [(point: GrowthDataPoint, value: Double, seg: Int)] {
        percentSegments { $0.yoyChangePercent }
    }

    private var sectorSegments: [(point: GrowthDataPoint, value: Double, seg: Int)] {
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

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: Y-axis labels (fixed, never scrolls)
            VStack(spacing: 0) {
                barYAxisLabels

                // Spacer matching x-axis labels + value/YoY/sector label rows
                Spacer()
                    .frame(height: 20 + AppSpacing.md + (20 + AppSpacing.sm) * 3)
            }
            .frame(width: yAxisWidth)

            // Right column: scrollable chart area
            GeometryReader { geometry in
                let visibleWidth = Swift.max(geometry.size.width, 1)
                let count = dataPoints.count
                // Bars occupy `barsWidth`; `trailingInset` of empty space follows.
                // The inset is INSIDE `contentWidth`, so the chart (framed to
                // barsWidth) and the four manual label rows (framed to contentWidth)
                // share one coordinate space and stay aligned — and the newest
                // column's centered bar/label render INTO the inset instead of
                // clipping at the trailing scroll edge.
                let barsWidth = needsScroll
                    ? CGFloat(count) * (visibleWidth / visibleColumnCount)
                    : Swift.max(visibleWidth - trailingInset, 1)
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
            .frame(height: chartHeight + 20 + AppSpacing.md + (20 + AppSpacing.sm) * 3 + (needsScroll ? AppSpacing.md : 0))
        }
    }

    // MARK: - Chart Area

    private func chartArea(barsWidth: CGFloat) -> some View {
        // Bar width derived from the band so it scales with the column count
        // (the manual labels use the SAME barsWidth/count grid → they align).
        let band = barsWidth / CGFloat(Swift.max(dataPoints.count, 1))
        let barW = Swift.min(Swift.max(band * 0.6, 2), 28)
        return Chart {
            // Horizontal grid lines (behind everything)
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // Bar marks for absolute values — negative bars render red and
            // downward from the zero baseline (sign-aware yDomain).
            ForEach(dataPoints) { dataPoint in
                BarMark(
                    x: .value("Period", dataPoint.period),
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
                    x: .value("Period", item.point.period),
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
                    x: .value("Period", item.point.period),
                    y: .value("YoY", normalizeYoY(item.value))
                )
                .foregroundStyle(AppColors.growthYoYYellow)
                .symbolSize(50)
            }

            // Sector average line — dashed, also broken at periods with no benchmark.
            ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("Period", item.point.period),
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
                    x: .value("Period", item.point.period),
                    y: .value("Sector", normalizeYoY(item.value))
                )
                .foregroundStyle(AppColors.growthSectorGray)
                .symbolSize(35)
            }

        }
        // Pin categorical order so Swift Charts' band centers match the manual
        // columnWidth grid the label rows use (otherwise bars + labels drift,
        // worst at the trailing/newest column).
        .chartXScale(domain: dataPoints.map(\.period))
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
        // Evenly-spaced labels at the 0% / 33% / 66% / 100% plot positions of the
        // sign-aware domain (top→bottom). For an all-positive series lo==0 so the
        // bottom label is "0"; for negatives it shows the real negative floor.
        let hi = yDomain.upperBound
        let lo = yDomain.lowerBound
        let span = hi - lo
        return VStack {
            Text(formatLargeNumber(hi))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLargeNumber(hi - span / 3))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLargeNumber(lo + span / 3))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(lo < 0 ? formatLargeNumber(lo) : "0")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Manual label rows
    //
    // Each row positions Text at band centers over `barsWidth` (the SAME grid the
    // chart's bars use, with a pinned chartXScale), then frames to `totalWidth`
    // (= barsWidth + trailingInset) with .leading alignment — so the newest
    // column's centered, .fixedSize() label renders INTO the inset rather than
    // clipping at the trailing scroll edge.

    private func columnWidth(_ barsWidth: CGFloat) -> CGFloat {
        barsWidth / CGFloat(Swift.max(dataPoints.count, 1))
    }

    private func xAxisLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        let colW = columnWidth(barsWidth)
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(dataPoint.period)
                    .font(.system(size: labelFontSize, weight: .regular))
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: colW * CGFloat(index) + colW / 2, y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func valueLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        let colW = columnWidth(barsWidth)
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                Text(formatLargeNumber(dataPoint.value))
                    .font(.system(size: labelFontSize, weight: .semibold))
                    .foregroundColor(AppColors.growthBarBlue)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: colW * CGFloat(index) + colW / 2, y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func yoyPercentageLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        let colW = columnWidth(barsWidth)
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
                    .position(x: colW * CGFloat(index) + colW / 2, y: 10)
            }
        }
        .frame(width: totalWidth, height: 20, alignment: .leading)
    }

    private func sectorAverageLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        let colW = columnWidth(barsWidth)
        return ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                // nil sector value (no benchmark for this period) → muted "—".
                Text(dataPoint.sectorAverageYoY.map { fmtYoY($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .regular))
                    .foregroundColor(AppColors.growthSectorGray)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: colW * CGFloat(index) + colW / 2, y: 10)
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
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.0fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.0fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.0fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.0fK", number / 1_000)
        } else if absNumber >= 1 {
            return String(format: "%.1f", number)
        }
        return String(format: "%.2f", number)
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
