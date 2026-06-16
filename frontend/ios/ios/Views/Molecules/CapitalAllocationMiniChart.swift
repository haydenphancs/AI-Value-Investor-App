//
//  CapitalAllocationMiniChart.swift
//  ios
//
//  Molecule: compact dividends/buybacks (bars) + shares-outstanding (line)
//  chart for the Insider & Management → Capital Allocation card. A pared-down
//  cousin of SignalOfConfidenceChartView (Financials tab) at ~120pt.
//
//  Yield (%) / Capital ($) toggle (reused atom). Dual gray Y-axis: LEFT = bar
//  scale (% or $), RIGHT = shares-outstanding scale (line normalized onto the
//  bar band, so trailing ticks sit at the matching positions) with the CURRENT
//  share count emphasised at the dashed connector — same as Signal of
//  Confidence. Tap a quarter for a value popup.
//

import SwiftUI
import Charts

struct CapitalAllocationMiniChart: View {
    let dataPoints: [SignalOfConfidenceDataPoint]

    /// Quarter the user tapped — drives the popup. nil = hidden. Owned by the
    /// parent so a tap anywhere outside the chart can dismiss it.
    @Binding var selectedPeriod: String?

    /// Yield (%) vs Capital ($) — same toggle as the full chart.
    @State private var viewType: SignalOfConfidenceViewType = .yield

    private let chartHeight: CGFloat = 120
    private let barWidth: CGFloat = 9

    var body: some View {
        if dataPoints.count >= 2 {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Compact + left-aligned; words match the legend's caption size.
                SignalOfConfidenceViewToggle(
                    selectedView: $viewType,
                    font: AppTypography.caption,
                    horizontalPadding: AppSpacing.sm,
                    verticalPadding: 3,
                    innerPadding: 2
                )
                chart
            }
        }
    }

    private var chart: some View {
        Chart {
            barMarks
            shareMarks
            selectionMarks
        }
        .chartYScale(domain: 0...maxBarValue)
        // Inset the bars from the plot edges so the first/last x-labels have
        // room and don't get clipped.
        .chartXScale(range: .plotDimension(startPadding: 14, endPadding: 14))
        .chartXAxis {
            AxisMarks(values: xTickPeriods) { value in
                AxisValueLabel {
                    if let period = value.as(String.self) {
                        Text(period)
                            .font(.system(size: 11))
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize()
                    }
                }
            }
        }
        .chartYAxis {
            // LEFT — bar scale (% or $). Gray, with faint gridlines.
            AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine().foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(leftAxisLabel(v))
                            .font(.system(size: 11))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
            // RIGHT — shares. Gray, placed at the line's normalized positions so
            // labels line up with the trend; the CURRENT count is emphasised.
            AxisMarks(position: .trailing, values: sharesTicks.map(\.position)) { value in
                AxisValueLabel {
                    if let pos = value.as(Double.self), let tick = sharesTick(at: pos) {
                        Text(tick.label)
                            .font(.system(size: 11, weight: tick.isCurrent ? .semibold : .regular))
                            .foregroundColor(tick.isCurrent ? AppColors.textSecondary : AppColors.textMuted)
                    }
                }
            }
        }
        .chartLegend(.hidden)
        .chartOverlay { proxy in
            GeometryReader { geo in
                ZStack(alignment: .topLeading) {
                    Rectangle()
                        .fill(.clear)
                        .contentShape(Rectangle())
                        .gesture(
                            // Discrete tap (not a drag) → coexists with the
                            // report ScrollView. Tap a quarter to show the
                            // popup; tap it again (or elsewhere) to hide it.
                            SpatialTapGesture().onEnded { tap in
                                guard let plot = proxy.plotFrame else { return }
                                let x = tap.location.x - geo[plot].origin.x
                                let hit = proxy.value(atX: x, as: String.self)
                                selectedPeriod = (hit == selectedPeriod) ? nil : hit
                            }
                        )
                    currentSharesConnector(proxy, geo)
                }
            }
        }
        .frame(height: chartHeight)
        .animation(.spring(response: 0.3, dampingFraction: 0.75), value: selectedPeriod)
        .animation(.easeInOut(duration: 0.25), value: viewType)
    }

    // MARK: - Marks

    @ChartContentBuilder
    private var barMarks: some ChartContent {
        ForEach(dataPoints) { dp in
            BarMark(
                x: .value("Quarter", dp.period),
                y: .value("Dividends", viewType == .yield ? dp.dividendYield : dp.dividendAmount),
                width: .fixed(barWidth)
            )
            .foregroundStyle(AppColors.confidenceDividends)
            .cornerRadius(2)
            .position(by: .value("Type", "Dividends"))
        }
        ForEach(dataPoints) { dp in
            BarMark(
                x: .value("Quarter", dp.period),
                y: .value("Buybacks", viewType == .yield ? dp.buybackYield : dp.buybackAmount),
                width: .fixed(barWidth)
            )
            .foregroundStyle(AppColors.confidenceBuybacks)
            .cornerRadius(2)
            .position(by: .value("Type", "Buybacks"))
        }
    }

    @ChartContentBuilder
    private var shareMarks: some ChartContent {
        ForEach(dataPoints) { dp in
            LineMark(
                x: .value("Quarter", dp.period),
                y: .value("Shares", normalizeShares(dp.sharesOutstanding))
            )
            .foregroundStyle(AppColors.confidenceSharesOutstanding)
            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            .interpolationMethod(.linear)
        }
        ForEach(dataPoints) { dp in
            PointMark(
                x: .value("Quarter", dp.period),
                y: .value("Shares", normalizeShares(dp.sharesOutstanding))
            )
            .foregroundStyle(AppColors.confidenceSharesOutstanding)
            .symbolSize(26)
        }
    }

    /// Dashed gray segment from the current (newest) share dot rightward to the
    /// right axis only — short on purpose, so it reads as belonging to the RIGHT
    /// (shares) axis instead of spanning the whole plot across both axes. Ends
    /// at the emphasised "current" right-axis tick.
    @ViewBuilder
    private func currentSharesConnector(_ proxy: ChartProxy, _ geo: GeometryProxy) -> some View {
        if let newest = dataPoints.last,
           let plot = proxy.plotFrame.map({ geo[$0] }),
           let x = proxy.position(forX: newest.period),
           let y = proxy.position(forY: normalizeShares(newest.sharesOutstanding)) {
            Path { path in
                path.move(to: CGPoint(x: plot.minX + x, y: plot.minY + y))
                path.addLine(to: CGPoint(x: plot.maxX, y: plot.minY + y))
            }
            .stroke(
                AppColors.textMuted.opacity(0.6),
                style: StrokeStyle(lineWidth: 1, dash: [4, 3])
            )
            .allowsHitTesting(false)
        }
    }

    /// Vertical indicator + value popup for the tapped quarter.
    @ChartContentBuilder
    private var selectionMarks: some ChartContent {
        if let sel = selectedPeriod, let dp = selectedPoint {
            RuleMark(x: .value("Quarter", sel))
                .foregroundStyle(AppColors.textMuted.opacity(0.35))
                .lineStyle(StrokeStyle(lineWidth: 1))
                .annotation(
                    position: .top,
                    spacing: 2,
                    overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))
                ) {
                    popup(dp)
                }
        }
    }

    // MARK: - Popup

    private func popup(_ dp: SignalOfConfidenceDataPoint) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(dp.period)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textPrimary)
            HStack(spacing: AppSpacing.sm) {
                popupMetric(AppColors.confidenceDividends,
                            viewType == .yield ? String(format: "%.2f%%", dp.dividendYield) : formatMoney(dp.dividendAmount))
                popupMetric(AppColors.confidenceBuybacks,
                            viewType == .yield ? String(format: "%.2f%%", dp.buybackYield) : formatMoney(dp.buybackAmount))
                popupMetric(AppColors.confidenceSharesOutstanding, formatShares(dp.sharesOutstanding))
            }
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
                .shadow(color: .black.opacity(0.15), radius: 6, x: 0, y: 3)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
        )
        .fixedSize()
    }

    private func popupMetric(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 3) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    private var selectedPoint: SignalOfConfidenceDataPoint? {
        guard let sel = selectedPeriod else { return nil }
        return dataPoints.first { $0.period == sel }
    }

    // MARK: - X-axis ticks (evenly spaced, anchored at the newest quarter)

    private var xTickPeriods: [String] {
        let n = dataPoints.count
        guard n > 4 else { return dataPoints.map(\.period) }
        // ~4 ticks, stepping back from the LAST column by a fixed stride so the
        // gaps are equal (avoids a lopsided final gap) and the newest quarter is
        // always labeled.
        let stride = max(1, Int((Double(n - 1) / 3.0).rounded()))
        var idxs: [Int] = []
        var i = n - 1
        while i >= 0 {
            idxs.append(i)
            i -= stride
        }
        return idxs.sorted().map { dataPoints[$0].period }
    }

    // MARK: - Scaling

    /// Top of the bar scale (yield % or capital $). Floored so the Y domain is
    /// always valid — when bars are ~0 (pure dilution) the floor keeps the
    /// shares line on a readable band instead of collapsing to zero height.
    private var maxBarValue: Double {
        let maxStacked = dataPoints.map {
            viewType == .yield ? ($0.dividendYield + $0.buybackYield)
                               : ($0.dividendAmount + $0.buybackAmount)
        }.max() ?? 0
        return max(maxStacked * 1.15, viewType == .yield ? 0.5 : 1.0)
    }

    /// Lightly padded shares range so the line doesn't touch the top/bottom
    /// edges; the slope still amplifies a tight series (e.g. 1000M→1037M).
    private var sharesBand: (min: Double, max: Double) {
        let values = dataPoints.map { $0.sharesOutstanding }
        let lo = values.min() ?? 0
        let hi = values.max() ?? 1
        let spread = hi - lo
        guard spread > .ulpOfOne else { return (lo - 1, hi + 1) }
        let pad = spread * 0.15
        return (lo - pad, hi + pad)
    }

    /// Map a shares value onto the full bar Y-scale (so the right-axis ticks,
    /// placed at these same normalized positions, line up with the line).
    private func normalizeShares(_ shares: Double) -> Double {
        let band = sharesBand
        let range = band.max - band.min
        guard range > 0 else { return maxBarValue * 0.5 }
        return maxBarValue * ((shares - band.min) / range)
    }

    /// Right-axis ticks: real min / mid / max share counts at their normalized Y
    /// positions, plus the CURRENT (newest) count emphasised. When current sits
    /// near a static tick it replaces it (so the labels never collide).
    private var sharesTicks: [(position: Double, label: String, isCurrent: Bool)] {
        let values = dataPoints.map { $0.sharesOutstanding }
        guard let lo = values.min(), let hi = values.max(),
              let newest = dataPoints.last?.sharesOutstanding else { return [] }
        let mid = (lo + hi) / 2
        var ticks: [(position: Double, label: String, isCurrent: Bool)] = [
            (normalizeShares(lo), formatShares(lo), false),
            (normalizeShares(mid), formatShares(mid), false),
            (normalizeShares(hi), formatShares(hi), false),
        ]
        let newestPos = normalizeShares(newest)
        if let i = ticks.firstIndex(where: { abs($0.position - newestPos) < maxBarValue * 0.08 }) {
            ticks[i] = (newestPos, formatShares(newest), true)
        } else {
            ticks.append((newestPos, formatShares(newest), true))
        }
        return ticks
    }

    private func sharesTick(at position: Double) -> (position: Double, label: String, isCurrent: Bool)? {
        sharesTicks.first { abs($0.position - position) < 0.0001 }
    }

    // MARK: - Formatters

    private func leftAxisLabel(_ value: Double) -> String {
        viewType == .yield ? String(format: "%.1f%%", value) : formatMoney(value)
    }

    /// Dollar amounts arrive in millions.
    private func formatMoney(_ millions: Double) -> String {
        if millions >= 1000 { return String(format: "$%.1fB", millions / 1000) }
        return String(format: "$%.0fM", millions)
    }

    /// Shares are in millions; pick ONE unit for the whole axis (off the max)
    /// so labels don't mix M and B.
    private func formatShares(_ value: Double) -> String {
        let maxShares = dataPoints.map { $0.sharesOutstanding }.max() ?? 0
        if maxShares >= 1000 {
            return String(format: "%.2fB", value / 1000)
        }
        return String(format: "%.0fM", value)
    }
}

#Preview {
    @Previewable @State var selected: String?
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.lg) {
            CapitalAllocationMiniChart(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                selectedPeriod: $selected
            )
            SignalOfConfidenceLegendView()
        }
        .padding()
    }
}
