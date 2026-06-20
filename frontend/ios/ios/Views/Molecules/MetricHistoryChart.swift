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

    private var chart: some View {
        Chart {
            ForEach(valued) { point in
                BarMark(
                    x: .value("Period", point.period),
                    // Clamp the drawn height into the robust domain so one extreme
                    // year (e.g. a P/E spike to 5000×) pins to the chart edge
                    // instead of flattening every other bar to a sliver.
                    y: .value("Value", clamped(point.value ?? 0))
                )
                .foregroundStyle(color(for: point))
                .cornerRadius(3)
            }
            // Sector-average overlay: a dashed gray line + dots, aligned to the
            // same x-categories as the bars (mirrors GrowthChartView).
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
        .chartYScale(domain: yDomain)
        .chartXScale(domain: orderedPeriods)
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
            AxisMarks(values: tickPeriods) { value in
                AxisValueLabel(centered: false) {
                    if let s = value.as(String.self) {
                        Text(s)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize()  // never ellipsize ("Q4…" → "Q4 '26")
                    }
                }
            }
        }
        // Horizontally scrollable: show a window of columns at a readable
        // width and let the user scroll back through history (both Annual &
        // Quarterly). The y-axis stays pinned. Starts at the most-recent end.
        .chartScrollableAxes(.horizontal)
        .chartXVisibleDomain(length: visibleColumns)
        .chartScrollPosition(initialX: scrollStart)
        .frame(height: 200)
    }

    /// How many bars are visible before scrolling kicks in. Fewer-than-window
    /// series (e.g. ~10 annual) just show in full (no scroll).
    private var visibleColumns: Int { Swift.min(orderedPeriods.count, 12) }

    /// Leading edge so the chart opens scrolled to the latest periods.
    private var scrollStart: String {
        orderedPeriods[Swift.max(0, orderedPeriods.count - visibleColumns)]
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

    /// Thin the x labels so dense (scrollable quarterly) charts don't crowd —
    /// roughly one label per 3–4 bars for long series, every other for short.
    /// Anchored to the end so the newest bar always keeps its label.
    private var tickPeriods: [String] {
        let n = orderedPeriods.count
        guard n > 8 else { return orderedPeriods }
        let step = n > 24 ? 4 : 2
        return orderedPeriods.enumerated()
            .filter { ($0.offset % step) == ((n - 1) % step) }
            .map(\.element)
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
