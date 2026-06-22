//
//  MetricHistoryChart.swift
//  ios
//
//  Molecule: a 5–10y (annual) / up to ~10y (quarterly) bar chart for ONE
//  fundamentals metric's time series — the tap-to-expand drill-down from the
//  Fundamentals & Growth cards. Unit-aware formatting ("percent" → 42.1%,
//  "x" → 35.8x, "score" → 12.5). The latest column is emphasized (bright blue);
//  negative bars render red. A dashed line overlays the sector average.
//
//  Rendering follows GrowthChartView's MANUAL pattern — a fixed left y-axis, a
//  horizontally-scrollable plot (Swift Charts with its OWN axes hidden), and
//  hand-positioned x-axis labels — because Swift Charts' native scrollable axis
//  drops/clips the newest column's label (no amount of domain padding fixes it;
//  native scroll clamps to the data extent). Manual labels are plain Text we
//  position, so the newest label ALWAYS renders; labels anchor on the newest
//  column and stride left by 2 ("alternating"). `.defaultScrollAnchor(.trailing)`
//  opens the scroll at the newest column.
//

import SwiftUI
import Charts

struct MetricHistoryChart: View {
    let points: [MetricHistoryPoint]   // oldest→newest (company)
    let unit: String?                  // "percent" | "x" | "score"
    var sector: [MetricHistoryPoint]? = nil  // sector-average overlay (aligned)

    private let chartHeight: CGFloat = 200
    private let yAxisWidth: CGFloat = 46
    private let xLabelHeight: CGFloat = 18
    private let xLabelTopGap: CGFloat = 6
    /// Columns visible before horizontal scrolling kicks in.
    private let visibleColumns: CGFloat = 12
    /// Empty space kept to the RIGHT of the last bar so the newest column's
    /// centered label (e.g. "Q4 '26") isn't cut off at the edge — shifts the
    /// columns left; the fixed y-axis column is untouched.
    private let trailingInset: CGFloat = 32
    /// Label every Nth column, ANCHORED on the newest (so the newest always
    /// shows): "alternating" = every other column.
    private let labelStride = 2

    /// Only points that actually have a value (gaps dropped so no phantom bars).
    private var valued: [MetricHistoryPoint] {
        points.filter { $0.value != nil }
    }

    /// ALL period labels (oldest→newest), INCLUDING periods whose value is nil
    /// (e.g. P/FCF when free cash flow is negative). The chart spans the full
    /// timeline so it ends on the same latest period as every other metric; a
    /// nil period renders as a red 0-line mark instead of a bar.
    private var orderedPeriods: [String] { points.map(\.period) }

    /// Sector points that have a value AND a matching company period (so the line
    /// aligns to the company x-categories — no floating endpoints). Spans the
    /// full timeline, so the sector line continues even where the company value
    /// is undefined (the red-mark periods).
    private var sectorValued: [MetricHistoryPoint] {
        let xs = Set(orderedPeriods)
        return (sector ?? []).filter { $0.value != nil && xs.contains($0.period) }
    }

    var body: some View {
        if valued.count < 2 {
            Text("Not enough history to chart.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, minHeight: 180)
        } else {
            chart
        }
    }

    private var chart: some View {
        HStack(alignment: .top, spacing: 0) {
            // Fixed y-axis (never scrolls), aligned to the plot's top.
            yAxisColumn
                .frame(width: yAxisWidth, height: chartHeight)

            // Scrollable plot + manual x-axis labels.
            GeometryReader { geo in
                let visibleWidth = Swift.max(geo.size.width, 1)
                let count = points.count
                let needsScroll = count > Int(visibleColumns)
                // Bars occupy `barsWidth`; `trailingInset` of empty space follows
                // so the newest column's label has room (columns shift left).
                let barsWidth = needsScroll
                    ? CGFloat(count) * (visibleWidth / visibleColumns)
                    : Swift.max(visibleWidth - trailingInset, 1)
                let contentWidth = needsScroll ? barsWidth + trailingInset : visibleWidth

                ScrollView(.horizontal, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 0) {
                        plot(width: barsWidth)
                            .frame(width: barsWidth, height: chartHeight)
                        xLabels(barsWidth: barsWidth, totalWidth: contentWidth)
                            .padding(.top, xLabelTopGap)
                    }
                    .frame(width: contentWidth, alignment: .leading)
                }
                .defaultScrollAnchor(.trailing)   // open at the newest column
            }
            .frame(height: chartHeight + xLabelTopGap + xLabelHeight)
        }
    }

    // MARK: - Plot (Swift Charts; its own axes hidden)

    private func plot(width: CGFloat) -> some View {
        let band = width / CGFloat(Swift.max(points.count, 1))
        let barW = Swift.min(Swift.max(band * 0.72, 2), 48)
        return Chart {
            // Gridlines at the SAME ticks as the fixed y-axis → they align.
            ForEach(yTicks, id: \.self) { v in
                RuleMark(y: .value("grid", v))
                    .foregroundStyle(AppColors.textMuted.opacity(0.15))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }
            ForEach(points) { point in
                if let v = point.value {
                    BarMark(
                        x: .value("Period", point.period),
                        // Clamp the drawn height into the robust domain so one extreme
                        // bar (e.g. a P/E spike to 5000×) pins to the edge instead of
                        // flattening every other bar to a sliver.
                        y: .value("Value", clamped(v)),
                        width: .fixed(barW)
                    )
                    .foregroundStyle(color(for: point))
                    .cornerRadius(3)
                } else {
                    // Negative / undefined ratio (e.g. negative FCF → P/FCF) keeps
                    // its slot in the timeline but shows a red mark at the 0 line
                    // instead of a bar (mirrors SmartMoneyFlowChart's zero marker).
                    RectangleMark(
                        x: .value("Period", point.period),
                        y: .value("Value", 0),
                        width: .fixed(barW),
                        height: .fixed(3)
                    )
                    .foregroundStyle(AppColors.bearish)
                    .cornerRadius(1)
                }
            }
            // Sector-average overlay: a dashed gray line + dots at the company
            // categories (mirrors GrowthChartView).
            ForEach(sectorValued) { point in
                LineMark(
                    x: .value("Period", point.period),
                    y: .value("Sector", clamped(point.value ?? 0)),
                    series: .value("Series", "Sector avg")
                )
                .foregroundStyle(AppColors.textSecondary)
                .lineStyle(StrokeStyle(lineWidth: 2, dash: [5, 4]))
                .interpolationMethod(.catmullRom)
                PointMark(
                    x: .value("Period", point.period),
                    y: .value("Sector", clamped(point.value ?? 0))
                )
                .foregroundStyle(AppColors.textSecondary)
                .symbolSize(18)
            }
        }
        .chartXScale(domain: orderedPeriods)   // pin chronological category order
        .chartYScale(domain: yDomain)
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartPlotStyle { $0.background(Color.clear) }
    }

    // MARK: - Fixed y-axis (manual labels, aligned to the gridlines)

    private var yAxisColumn: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let lo = yDomain.lowerBound
            let span = yDomain.upperBound - lo
            ForEach(yTicks, id: \.self) { v in
                Text(format(v))
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .frame(width: yAxisWidth - 4, alignment: .trailing)
                    .position(
                        x: (yAxisWidth - 4) / 2,
                        y: span > 0 ? h * (1 - (v - lo) / span) : h / 2
                    )
            }
        }
    }

    // MARK: - Manual x-axis labels (anchored on the newest column)

    private func xLabels(barsWidth: CGFloat, totalWidth: CGFloat) -> some View {
        let count = points.count
        let columnWidth = barsWidth / CGFloat(Swift.max(count, 1))
        return ZStack(alignment: .topLeading) {
            ForEach(Array(points.enumerated()), id: \.offset) { index, point in
                // Anchor on the newest (index == count-1) and stride left → the
                // newest column is ALWAYS labeled.
                if (count - 1 - index) % labelStride == 0 {
                    Text(point.period)
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .fixedSize()   // never ellipsize ("Q4…" → "Q4 '26")
                        .position(
                            x: columnWidth * CGFloat(index) + columnWidth / 2,
                            y: xLabelHeight / 2
                        )
                }
            }
        }
        // Frame spans the FULL content (bars + trailing inset) so the last
        // label can render into the trailing space instead of being clipped.
        .frame(width: totalWidth, height: xLabelHeight, alignment: .leading)
    }

    // MARK: - Helpers

    /// Emphasize the latest bar; flag negatives red.
    private func color(for point: MetricHistoryPoint) -> Color {
        if (point.value ?? 0) < 0 { return AppColors.bearish }
        let isLatest = point.id == valued.last?.id
        return isLatest ? AppColors.primaryBlue : AppColors.primaryBlue.opacity(0.5)
    }

    /// Clamp a drawn value into the robust domain (outliers — company OR sector —
    /// pin to the edge instead of escaping the frame).
    private func clamped(_ v: Double) -> Double {
        Swift.min(Swift.max(v, yDomain.lowerBound), yDomain.upperBound)
    }

    /// Outlier-robust y-axis range that ALWAYS includes zero (so the baseline is
    /// visible even for an all-negative series) and clamps only GENUINELY extreme
    /// bars to the edge (a P/E of 5000×, an interest-coverage of −5000×). Modest
    /// negatives (e.g. a −2.9% ROA quarter) render at TRUE height: the old lower
    /// fence (`q1 − 1.5·iqr`) could land above zero for a low-magnitude series,
    /// driving `bottom` to 0 and collapsing negative bars onto the baseline.
    private var yDomain: ClosedRange<Double> {
        let vals = (valued.compactMap(\.value) + sectorValued.compactMap(\.value)).sorted()
        guard let lo = vals.first, let hi = vals.last else { return 0...1 }
        let q1 = vals[vals.count / 4]
        let q3 = vals[min(vals.count - 1, (vals.count * 3) / 4)]
        // IQR with a floor so a flat series (iqr≈0) still gets a sane window.
        let iqr = Swift.max(q3 - q1, abs(q3) * 0.1, 1)
        // A value within ±3·scale is treated as legitimate and shown at true
        // height; only beyond that does it clamp to the edge.
        let scale = Swift.max(abs(q3), abs(q1), iqr)
        let fenceHi = Swift.min(hi, q3 + 1.5 * iqr)
        let fenceLo = Swift.max(lo, -3.0 * scale)
        var top = Swift.max(fenceHi, 0)
        var bottom = Swift.min(fenceLo, 0)
        if top == bottom { top = bottom + 1 }
        let span = top - bottom
        // Pad outward (only away from zero) so bars don't touch the frame.
        top += (top > 0) ? span * 0.08 : 0
        bottom -= (bottom < 0) ? span * 0.08 : 0
        return bottom...top
    }

    /// Explicit y-axis ticks anchored on 0 and stepped outward by a "nice"
    /// increment, so the negative region ALWAYS gets a labeled tick when the
    /// domain goes below zero. `bottom` ≤ 0 ≤ `top` always (domain anchors zero).
    private var yTicks: [Double] {
        let lo = yDomain.lowerBound, hi = yDomain.upperBound
        guard hi > lo else { return [lo, hi] }
        let step = niceStep((hi - lo) / 4)
        guard step > 0 else { return [lo, 0, hi] }
        var ticks: [Double] = [0]
        var t = step
        while t <= hi + step * 1e-6 { ticks.append(t); t += step }
        t = -step
        while t >= lo - step * 1e-6 { ticks.append(t); t -= step }
        return ticks.sorted()
    }

    /// Round a raw step to the nearest 1/2/5 × 10ⁿ for clean axis labels.
    private func niceStep(_ raw: Double) -> Double {
        guard raw > 0, raw.isFinite else { return 1 }
        let mag = pow(10, (log10(raw)).rounded(.down))
        let n = raw / mag
        let nice: Double = n < 1.5 ? 1 : (n < 3 ? 2 : (n < 7 ? 5 : 10))
        return nice * mag
    }

    private func format(_ v: Double) -> String {
        switch unit {
        case "percent": return String(format: "%.1f%%", v)
        case "score":   return String(format: "%.1f", v)
        default:        return String(format: "%.1fx", v)  // "x" / nil
        }
    }
}

#Preview {
    // Annual: ~12 years (non-scroll) — newest year labeled, then every other.
    let annual = (2014...2025).map { yr in
        MetricHistoryPoint(period: String(yr), value: 30 + Double(yr - 2014) * 1.2)
    }
    // Quarterly-ish: many columns (scrolls) — newest labeled, alternating.
    let quarterly: [MetricHistoryPoint] = (0..<24).map { i in
        let yr = 2020 + i / 4
        let q = i % 4 + 1
        return MetricHistoryPoint(period: "Q\(q) '\(yr % 100)",
                                  value: i == 10 ? -3.0 : 2.0 + Double(i % 5))
    }
    return VStack(spacing: 24) {
        MetricHistoryChart(points: annual, unit: "percent")
        MetricHistoryChart(points: quarterly, unit: "x")
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
