//
//  MetricHistoryChart.swift
//  ios
//
//  Molecule: a simple 5–10y bar chart for ONE fundamentals metric's time
//  series (the tap-to-expand drill-down from the Fundamentals & Growth
//  cards). Unit-aware formatting: "percent" → 42.1%, "x" → 35.8x,
//  "score" → 12.5. Negative bars (e.g. negative FCF growth) render red; the
//  latest bar is emphasized so the eye lands on "now" versus the trend.
//

import SwiftUI
import Charts

struct MetricHistoryChart: View {
    let points: [MetricHistoryPoint]   // oldest→newest (company)
    let unit: String?                  // "percent" | "x" | "score"
    var sector: [MetricHistoryPoint]? = nil  // sector-average overlay (aligned)

    /// Only the points that actually have a value (gaps are dropped so the
    /// chart doesn't render zero-height phantom bars).
    private var valued: [MetricHistoryPoint] {
        points.filter { $0.value != nil }
    }

    private var orderedPeriods: [String] { valued.map(\.period) }

    /// Sector points that have a value AND a matching company bar (so the
    /// line aligns to the company x-categories — no floating endpoints).
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

    // Bars are plotted on a NUMERIC index x-axis (0, 1, 2, …) rather than a
    // categorical one — `chartXVisibleDomain` / `chartScrollPosition` only
    // window + scroll reliably against a numeric domain, which is what lets the
    // chart open at the latest period and scroll back through the full history.
    private var indexedCompany: [(idx: Double, point: MetricHistoryPoint)] {
        valued.enumerated().map { (Double($0.offset), $0.element) }
    }

    private var periodToIndex: [String: Double] {
        Dictionary(uniqueKeysWithValues:
            orderedPeriods.enumerated().map { ($0.element, Double($0.offset)) })
    }

    private var chart: some View {
        Chart {
            ForEach(indexedCompany, id: \.idx) { item in
                BarMark(
                    x: .value("i", item.idx),
                    // Clamp the drawn height into the robust domain so one extreme
                    // year (e.g. a P/E spike to 5000×) pins to the chart edge
                    // instead of flattening every other bar to a sliver.
                    y: .value("Value", clamped(item.point.value ?? 0)),
                    width: .ratio(0.72)
                )
                .foregroundStyle(color(for: item.point))
                .cornerRadius(3)
            }
            // Sector-average overlay: a dashed gray line + dots at the SAME
            // index positions as the bars (mirrors GrowthChartView).
            ForEach(sectorValued) { point in
                if let xi = periodToIndex[point.period] {
                    LineMark(
                        x: .value("i", xi),
                        y: .value("Sector", clamped(point.value ?? 0)),
                        series: .value("Series", "Sector avg")
                    )
                    .foregroundStyle(AppColors.textSecondary)
                    .lineStyle(StrokeStyle(lineWidth: 2, dash: [5, 4]))
                    .interpolationMethod(.catmullRom)
                    PointMark(
                        x: .value("i", xi),
                        y: .value("Sector", clamped(point.value ?? 0))
                    )
                    .foregroundStyle(AppColors.textSecondary)
                    .symbolSize(18)
                }
            }
        }
        .chartYScale(domain: yDomain)
        .chartXScale(domain: -0.5 ... (Double(orderedPeriods.count) - 0.5))
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine()
                    .foregroundStyle(AppColors.textMuted.opacity(0.15))
                AxisValueLabel {
                    if let d = value.as(Double.self) {
                        Text(format(d))
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: tickIndices) { value in
                AxisValueLabel(centered: true) {
                    if let d = value.as(Double.self), let label = labelAt(d) {
                        Text(label)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize()  // never ellipsize ("Q4…" → "Q4 '26")
                    }
                }
            }
        }
        // Horizontally scrollable: show a window of columns at a readable width,
        // opening at the most-recent end; the y-axis stays pinned.
        .chartScrollableAxes(.horizontal)
        .chartXVisibleDomain(length: Double(visibleColumns))
        .chartScrollPosition(initialX: Double(max(0, orderedPeriods.count - visibleColumns)))
        .frame(height: 200)
    }

    /// How many bars are visible before scrolling kicks in. Fewer-than-window
    /// series (e.g. ~10 annual) just show in full (no scroll).
    private var visibleColumns: Int { Swift.min(orderedPeriods.count, 12) }

    /// Index of the period to use as a label tick — thinned for dense charts.
    private var tickIndices: [Double] {
        let n = orderedPeriods.count
        guard n > 8 else { return (0..<n).map(Double.init) }
        let step = n > 24 ? 4 : 2
        return stride(from: (n - 1) % step, to: n, by: step).map(Double.init)
    }

    private func labelAt(_ d: Double) -> String? {
        let i = Int(d.rounded())
        guard i >= 0, i < orderedPeriods.count else { return nil }
        return orderedPeriods[i]
    }

    // MARK: - Helpers

    /// Emphasize the latest bar; flag negatives red.
    private func color(for point: MetricHistoryPoint) -> Color {
        if (point.value ?? 0) < 0 { return AppColors.bearish }
        let isLatest = point.id == valued.last?.id
        return isLatest ? AppColors.primaryBlue : AppColors.primaryBlue.opacity(0.5)
    }

    /// Clamp a drawn value into the robust domain (keeps outliers — company OR
    /// sector — pinned to the edge instead of escaping the frame).
    private func clamped(_ v: Double) -> Double {
        Swift.min(Swift.max(v, yDomain.lowerBound), yDomain.upperBound)
    }

    /// Outlier-robust y-axis range that ALWAYS includes zero (so the baseline
    /// is visible even for an all-negative series) and caps the upper/lower
    /// bound with an IQR fence (so a single extreme bar can't flatten the
    /// rest — same idea as GrowthChartView / EarningsTimelineChart). Values
    /// beyond the fence are clamped to the edge when drawn.
    private var yDomain: ClosedRange<Double> {
        // Include sector values so the overlaid line stays in-frame; the IQR
        // fence below still clamps any single outlier (company or sector).
        let vals = (valued.compactMap(\.value) + sectorValued.compactMap(\.value)).sorted()
        guard let lo = vals.first, let hi = vals.last else { return 0...1 }
        let q1 = vals[vals.count / 4]
        let q3 = vals[min(vals.count - 1, (vals.count * 3) / 4)]
        // IQR with a floor so a flat series (iqr≈0) still gets a sane window.
        let iqr = Swift.max(q3 - q1, abs(q3) * 0.1, 1)
        let fenceHi = Swift.min(hi, q3 + 1.5 * iqr)
        let fenceLo = Swift.max(lo, q1 - 1.5 * iqr)
        // Anchor zero so the baseline is always on-screen.
        var top = Swift.max(fenceHi, 0)
        var bottom = Swift.min(fenceLo, 0)
        if top == bottom { top = bottom + 1 }
        let span = top - bottom
        // Pad outward (only away from zero) so bars don't touch the frame.
        top += (top > 0) ? span * 0.08 : 0
        bottom -= (bottom < 0) ? span * 0.08 : 0
        return bottom...top
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
    let pts = (2015...2024).map { yr in
        MetricHistoryPoint(period: String(yr), value: Double(yr - 2010) * 4.0)
    }
    VStack(spacing: 24) {
        MetricHistoryChart(points: pts, unit: "percent")
        MetricHistoryChart(
            points: [
                MetricHistoryPoint(period: "2021", value: 12.0),
                MetricHistoryPoint(period: "2022", value: -8.2),
                MetricHistoryPoint(period: "2023", value: 5.0),
                MetricHistoryPoint(period: "2024", value: 9.1),
            ],
            unit: "percent"
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
