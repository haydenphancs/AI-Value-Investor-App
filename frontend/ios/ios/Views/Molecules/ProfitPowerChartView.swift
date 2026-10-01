//
//  ProfitPowerChartView.swift
//  ios
//
//  Molecule: Multi-line chart displaying profit margin metrics over time
//  Uses native Swift Charts framework with horizontal scrolling for deep history
//

import SwiftUI
import Charts

struct ProfitPowerChartView: View {
    let dataPoints: [ProfitPowerDataPoint]
    @Binding var selectedDataPoint: ProfitPowerDataPoint?
    /// "Industry" / "Sector" for the benchmark line's tooltip label, matching
    /// the legend. Defaults to "Sector" for callers that don't plumb it.
    var peerWord: String = "Sector"
    /// True when the build lost a company data leg upstream (the server's `degraded`): an
    /// empty series is then an outage, and the empty state must not describe the company.
    var isDegraded: Bool = false
    /// Pending tooltip auto-dismiss, so a new tap replaces the old timer.
    @State private var tooltipDismissTask: DispatchWorkItem?

    // Chart configuration
    private let chartHeight: CGFloat = 240
    private let yAxisWidth: CGFloat = 40
    private let visibleColumnCount: CGFloat = 6  // columns visible before scrolling kicks in
    private let xAxisHeight: CGFloat = 20

    /// The series drawn, in z-order (later on top) — also the stacking order of the
    /// off-scale value labels on one column.
    private static let plottedTypes: [ProfitMarginType] = [
        .grossMargin, .netMargin, .sectorAverage, .operatingMargin, .fcfMargin,
    ]

    /// Every non-nil margin on screen. `compactMap` — a nil margin is absent
    /// data (a bank has no gross profit), not a 0.
    private var allValues: [Double] {
        dataPoints.flatMap { p in Self.plottedTypes.compactMap { p.margin(for: $0) } }
    }

    /// True when there is no COMPANY margin to plot — the caller shows an empty state
    /// instead of a fabricated axis. The peer line alone is not a Profit Power chart:
    /// a pre-revenue year is now a gap (all four margins nil) rather than a dropped row.
    private var hasData: Bool {
        dataPoints.contains { p in
            [p.grossMargin, p.operatingMargin, p.fcfMargin, p.netMargin]
                .contains { $0?.isFinite == true }
        }
    }

    /// |margin| (%) at or below which a value ALWAYS sets the axis. A margin beyond ±100%
    /// only comes from a near-zero revenue period (RIVN 2021 at −8,524% net, a biotech's
    /// −40,000% quarter); an ordinary year — a 60% one-off gain, a −30% impairment —
    /// never reaches it.
    private static let extremeMarginPct: Double = 100

    /// The values that SET the axis: every finite margin except one that is BOTH extreme
    /// in size (|v| > `extremeMarginPct`) AND outside the pooled robust fence
    /// (`ChartDomain.robust`). The fence alone is not a test for "outlier": it pools all
    /// five series, so a gross line sitting above four low ones (WMT 24% vs 1-5%, COST,
    /// AMZN, any software company) lies beyond it while being perfectly ordinary — it was
    /// flattened onto the edge with an arrow on every column. The size test alone would
    /// pin a real −150% year of a loss-maker whose whole history sits there.
    private var axisValues: [Double] {
        let finite = allValues.filter { $0.isFinite }
        let fence = ChartDomain.robust(
            finite, includeZero: true, headroomFraction: 0.0, fallback: 0...50
        )
        return finite.filter { abs($0) <= Self.extremeMarginPct || fence.contains($0) }
    }

    /// Chart bounds: the old rounded min/max axis (every value rounded outward to a
    /// multiple of 10), built from `axisValues` — so ordinary data keeps EXACTLY the old
    /// axis, and only an extreme tiny-revenue period is left out of it. That period is
    /// drawn CLAMPED to the plot edge with an off-scale arrow and its true value
    /// (`offScaleMarks`); the tooltip always shows the true number. `make` keeps an
    /// all-zero or all-negative series from producing a zero-width (crash) or inverted
    /// domain.
    private var marginDomain: ClosedRange<Double> {
        let values = axisValues
        let rounded = values.map { ceil($0 / 10) * 10 } + values.map { floor($0 / 10) * 10 }
        return ChartDomain.make(
            rounded, includeZero: true, headroomFraction: 0.0, fallback: 0...50
        )
    }

    /// A 0% rule is drawn (and labelled) whenever the axis spans profit AND loss: the
    /// grid lines fall on arbitrary values (8 / 26 / 44 / 62 for −10…80), so a +3% and a
    /// −3% net margin used to sit in the same unlabelled band.
    private var showsZeroRule: Bool { minMargin < 0 && maxMargin > 0 }

    private var maxMargin: Double { marginDomain.upperBound }
    private var minMargin: Double { marginDomain.lowerBound }

    // Grid line values (5 horizontal lines). Was
    // `stride(from:to:by: (max-min)/5)`, which is a HARD CRASH when every
    // margin is 0 (`stride` traps on a zero step).
    private var gridValues: [Double] {
        ChartDomain.gridValues(in: marginDomain, count: 4)
    }

    // Consistent sizes for both Annual and Quarterly
    private let symbolSize: CGFloat = 40
    private let lineWidth: CGFloat = 2.5

    // MARK: - Plotted geometry

    /// One plotted vertex: its column, the value DRAWN (clamped into the domain) and the
    /// TRUE value. `seg` increments after every nil, so each contiguous run is its own
    /// line series and a missing period renders as a BREAK, not a straight bridge.
    private struct PlotVertex: Identifiable {
        let index: Int
        let drawn: Double
        let actual: Double
        let seg: Int
        let isOffScale: Bool
        var id: Int { index }
    }

    private func vertices(for type: ProfitMarginType) -> [PlotVertex] {
        let domain = marginDomain
        var out: [PlotVertex] = []
        var seg = 0
        var prevWasNil = true
        for (index, point) in dataPoints.enumerated() {
            guard let value = point.margin(for: type), value.isFinite else {
                prevWasNil = true
                continue
            }
            if prevWasNil {
                seg += 1
                prevWasNil = false
            }
            out.append(PlotVertex(
                index: index,
                drawn: ChartDomain.clamp(value, to: domain),
                actual: value,
                seg: seg,
                isOffScale: ChartDomain.isOffScale(value, in: domain)
            ))
        }
        return out
    }

    /// A margin beyond the robust domain, drawn PINNED to the plot edge as an outward
    /// arrow with its true value beside it (owner decision, 2026-09-30: cap at the edge,
    /// mark it, print the real number). `rank` stacks the labels of several series
    /// pinned on the same column and edge so they never print on top of each other.
    private struct OffScaleMarker: Identifiable {
        let id: String
        let index: Int
        let edgeValue: Double
        let actual: Double
        let seriesName: String
        let color: Color
        let isAbove: Bool
        let rank: Int
    }

    private var offScaleMarkers: [OffScaleMarker] {
        let domain = marginDomain
        var out: [OffScaleMarker] = []
        var nextRank: [String: Int] = [:]
        for type in Self.plottedTypes {
            for vertex in vertices(for: type) where vertex.isOffScale {
                let isAbove = vertex.actual > domain.upperBound
                let slot = "\(vertex.index)-\(isAbove)"
                let rank = nextRank[slot, default: 0]
                nextRank[slot] = rank + 1
                out.append(OffScaleMarker(
                    id: "\(type.rawValue)-\(vertex.index)",
                    index: vertex.index,
                    edgeValue: vertex.drawn,
                    actual: vertex.actual,
                    seriesName: type.rawValue,
                    color: type.color,
                    isAbove: isAbove,
                    rank: rank
                ))
            }
        }
        return out
    }

    private var needsScroll: Bool {
        dataPoints.count > Int(visibleColumnCount)
    }

    var body: some View {
        if hasData {
            chartBody
        } else {
            // No plottable margin anywhere in the series. Drawing the chart
            // here rendered an invented 0–50% axis with no lines on it, which
            // reads as "margins are zero" rather than "we have no data". A DEGRADED build
            // (an upstream leg failed) emptied this series: an outage, not a company fact.
            ChartUnavailableView(message: isDegraded
                ? "Margin data is temporarily unavailable. Please try again shortly."
                : "Margin data isn't available for this company.")
                .frame(height: chartHeight + xAxisHeight + AppSpacing.sm)
        }
    }

    private var chartBody: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: fixed Y-axis labels (never scrolls)
            VStack(spacing: 0) {
                yAxisLabels

                // Spacer matching x-axis labels height
                Spacer()
                    .frame(height: xAxisHeight + AppSpacing.sm)
            }
            .frame(width: yAxisWidth)

            // Right column: scrollable chart area
            GeometryReader { geometry in
                let visibleWidth = geometry.size.width
                let contentWidth = needsScroll
                    ? CGFloat(dataPoints.count) * (visibleWidth / visibleColumnCount)
                    : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(spacing: 0) {
                        chartArea(contentWidth: contentWidth)
                            .frame(height: chartHeight)

                        xAxisLabels(plotWidth: contentWidth)
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: contentWidth)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + xAxisHeight + AppSpacing.sm + (needsScroll ? AppSpacing.md : 0))
        }
        // Overlay tooltip when a data point is selected — and only while that point is
        // still on THIS chart: the card keeps the selection across the Annual/Quarterly
        // toggle, which painted an annual year's margins over the quarterly series.
        .overlay(alignment: .top) {
            if let selectedDataPoint,
               dataPoints.contains(where: { $0.id == selectedDataPoint.id }) {
                ProfitPowerTooltipView(dataPoint: selectedDataPoint, peerWord: peerWord)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.top, AppSpacing.xs)
                    .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.7), value: selectedDataPoint?.id)
    }

    // MARK: - Chart Area

    private func chartArea(contentWidth: CGFloat) -> some View {
        Chart {
            // Horizontal grid lines
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // The profit / loss boundary, stronger than the grid (see `showsZeroRule`).
            if showsZeroRule {
                RuleMark(y: .value("Zero", 0))
                    .foregroundStyle(AppColors.textMuted.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }

            // Gross Margin Line (Blue - highest)
            marginLineMark(for: .grossMargin)
            marginPointMark(for: .grossMargin)

            // Net Margin Line (Green)
            marginLineMark(for: .netMargin)
            marginPointMark(for: .netMargin)

            // Sector Average Line (Gray - dashed)
            sectorAverageLineMark
            sectorAveragePointMark

            // Operating Margin Line (Orange)
            marginLineMark(for: .operatingMargin)
            marginPointMark(for: .operatingMargin)

            // FCF Margin Line (Purple)
            marginLineMark(for: .fcfMargin)
            marginPointMark(for: .fcfMargin)

            // Margins beyond the robust domain: pinned arrows + their true values.
            offScaleMarks
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: marginDomain)
        // A NUMERIC index axis (0…N-1) with a fixed edge pad, exactly like
        // GrowthChartView / ProfitabilityChartView — and the label row below positions
        // each label at `xCenter(index)`, the SAME pixel this scale gives the mark.
        // This used to be a categorical `period` axis over a flush-divided HStack of
        // labels; Swift Charts' band placement and the HStack columns are different
        // geometries, so every dot sat about half a column LEFT of its year / quarter
        // (TestFlight, INTC Profit Power, 2026-09-17).
        .chartXScale(domain: xDomain(), range: .plotDimension(padding: edgeLabelPad))
        // Not `.clipped()`: every mark is drawn at a CLAMPED value (`vertex.drawn`), so no
        // line or dot can leave the plot — and a clip would cut the edge dots in half.
        // Clamping, not clipping, is the overflow guard. The off-scale arrows are shifted
        // inside the plot (`offScaleGlyphInset`), because the enclosing horizontal
        // ScrollView DOES clip at the chart's top edge.
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
        .contentShape(Rectangle())
        .onTapGesture { location in
            updateSelection(at: location, chartWidth: contentWidth)
            // Cancel any previously scheduled dismissal: every tap used to
            // schedule an unconditional clear, so tapping B shortly after A
            // dismissed B early (and rapid taps left N pending closures).
            tooltipDismissTask?.cancel()
            let task = DispatchWorkItem { selectedDataPoint = nil }
            tooltipDismissTask = task
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5, execute: task)
        }
    }

    /// Room reserved at each plot edge for the widest centred edge label ("Q1 '25").
    /// In POINTS — label width is font-fixed, not a fraction of the chart.
    private let edgeLabelPad: CGFloat = 24

    /// Plain index domain 0…N-1; the edge spacing comes from `.plotDimension(padding:)`.
    private func xDomain() -> ClosedRange<Double> {
        let n = dataPoints.count
        guard n > 1 else { return -0.5 ... 0.5 }          // lone point: centred
        return 0.0 ... Double(n - 1)
    }

    /// Pixel centre of column `index` — mirrors the chart's scale so the label row and
    /// the tap mapping agree with the marks to the pixel.
    private func xCenter(_ index: Int, plotWidth: CGFloat) -> CGFloat {
        let n = dataPoints.count
        guard n > 1 else { return plotWidth / 2 }
        let usable = Swift.max(plotWidth - 2 * edgeLabelPad, 1)
        return edgeLabelPad + CGFloat(index) / CGFloat(n - 1) * usable
    }

    /// Inverse of `xCenter`: the column whose centre is nearest to `x`.
    private func nearestIndex(atX x: CGFloat, plotWidth: CGFloat) -> Int? {
        let n = dataPoints.count
        guard n > 0, x.isFinite, plotWidth.isFinite, plotWidth > 0 else { return nil }
        guard n > 1 else { return 0 }
        let usable = Swift.max(plotWidth - 2 * edgeLabelPad, 1)
        let raw = ((x - edgeLabelPad) / usable * CGFloat(n - 1)).rounded()
        guard raw.isFinite else { return nil }
        return Int(Swift.min(Swift.max(raw, 0), CGFloat(n - 1)))
    }

    // MARK: - Line Marks

    // Every builder draws `vertex.drawn` — the value CLAMPED into the domain — so no mark
    // can leave the plot. A nil margin has no vertex, and the per-run `seg` in the series
    // key BREAKS the line there: one constant series key per margin made Swift Charts
    // bridge the gap with a straight segment that read as a real value for the missing
    // period (the report's ProfitabilityChartView already segmented this way).

    @ChartContentBuilder
    private func marginLineMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(vertices(for: type)) { vertex in
            LineMark(
                x: .value("i", Double(vertex.index)),
                y: .value("Margin", vertex.drawn),
                series: .value("Series", "\(type.rawValue)-\(vertex.seg)")
            )
            .foregroundStyle(type.color)
            .lineStyle(StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
        }
    }

    /// Dots for in-range vertices only; a pinned vertex gets its arrow in `offScaleMarks`.
    @ChartContentBuilder
    private func marginPointMark(for type: ProfitMarginType) -> some ChartContent {
        ForEach(vertices(for: type).filter { !$0.isOffScale }) { vertex in
            PointMark(
                x: .value("i", Double(vertex.index)),
                y: .value("Margin", vertex.drawn)
            )
            .foregroundStyle(type.color)
            .symbolSize(symbolSize)
        }
    }

    @ChartContentBuilder
    private var sectorAverageLineMark: some ChartContent {
        ForEach(vertices(for: .sectorAverage)) { vertex in
            LineMark(
                x: .value("i", Double(vertex.index)),
                y: .value("Sector", vertex.drawn),
                series: .value("Series", "SectorAverage-\(vertex.seg)")
            )
            .foregroundStyle(AppColors.profitSectorAverage)
            .lineStyle(StrokeStyle(lineWidth: lineWidth - 0.5, lineCap: .round, lineJoin: .round, dash: [6, 4]))
        }
    }

    @ChartContentBuilder
    private var sectorAveragePointMark: some ChartContent {
        ForEach(vertices(for: .sectorAverage).filter { !$0.isOffScale }) { vertex in
            PointMark(
                x: .value("i", Double(vertex.index)),
                y: .value("Sector", vertex.drawn)
            )
            .foregroundStyle(AppColors.profitSectorAverage)
            .symbolSize(symbolSize * 0.75)
        }
    }

    /// Pinned margins: an outward arrow ON the plot edge in the series colour (a chart
    /// mark, so the 3:1 series token is its role) and the TRUE value just inside the
    /// edge, stacked by `rank`. The arrow glyph is chrome — the mark itself carries the
    /// accessibility label and the true value.
    @ChartContentBuilder
    private var offScaleMarks: some ChartContent {
        ForEach(offScaleMarkers) { marker in
            PointMark(
                x: .value("i", Double(marker.index)),
                y: .value("Margin", marker.edgeValue)
            )
            .symbol {
                // Chrome, not content: hidden from VoiceOver (the mark carries the label),
                // so the 3:1 series token is the right floor for the glyph.
                // Shifted INSIDE the plot so its tip sits on the edge: centred on the
                // edge, the upper half of an up-arrow fell outside the chart frame and
                // the horizontal ScrollView clipped it to a flat stub.
                Image(systemName: marker.isAbove ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                    .accessibilityHidden(true)
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(marker.color)
                    .offset(y: marker.isAbove ? offScaleGlyphInset : -offScaleGlyphInset)
            }
            .annotation(
                position: marker.isAbove ? AnnotationPosition.bottom : AnnotationPosition.top,
                spacing: offScaleLabelSpacing(rank: marker.rank)
            ) {
                offScaleLabel(marker)
            }
            .accessibilityLabel("\(marker.seriesName), off the chart scale")
            .accessibilityValue(CompactNumberFormat.percentString(marker.actual))
        }
    }

    /// How far the pinned arrow is shifted inside the plot (about half its 9pt glyph), so
    /// the whole arrow is drawn: the chart's top edge is flush with the top of the
    /// horizontal ScrollView, which clips. Not `.scrollClipDisabled()` — a scrolled chart
    /// would then draw over the fixed y-axis column.
    private let offScaleGlyphInset: CGFloat = 5

    /// Gap between the pinned arrow and its label (clearing the inward-shifted glyph);
    /// each further series pinned on the same column and edge steps one label height
    /// further inside the plot.
    private func offScaleLabelSpacing(rank: Int) -> CGFloat {
        let step: CGFloat = 14
        return 3 + offScaleGlyphInset + CGFloat(rank) * step
    }

    private func offScaleLabel(_ marker: OffScaleMarker) -> some View {
        HStack(spacing: 2) {
            Circle()
                .fill(marker.color)
                .frame(width: 5, height: 5)
            Text(CompactNumberFormat.percentString(marker.actual))
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)
                .fixedSize()
        }
        .accessibilityHidden(true)
    }

    // MARK: - Y-Axis Labels

    /// Labels sit at the SAME values as the grid lines (both bounds + `gridValues`),
    /// positioned through the chart's own normalisation — the old VStack/Spacer column
    /// spread the labels by text height and drifted a few points off their lines. When
    /// the axis spans profit and loss a "0%" label joins them and any label closer than
    /// `minLabelGap` to it is dropped, so the two never overlap.
    private var yAxisLabelValues: [Double] {
        let ticks = [minMargin] + gridValues + [maxMargin]
        guard showsZeroRule else { return ticks }
        let zeroY = yLabelCenter(0)
        return ticks.filter { abs(yLabelCenter($0) - zeroY) >= minLabelGap } + [0]
    }

    private let minLabelGap: CGFloat = 12
    /// Half a caption line: keeps the top / bottom label inside the column, where the
    /// old VStack put them.
    private let labelHalfHeight: CGFloat = 7

    private func yLabelCenter(_ value: Double) -> CGFloat {
        let y = chartHeight * (1 - CGFloat(ChartDomain.normalize(value, in: marginDomain)))
        return Swift.min(Swift.max(y, labelHalfHeight), chartHeight - labelHalfHeight)
    }

    private var yAxisLabels: some View {
        let labelWidth = yAxisWidth - AppSpacing.xs
        return ZStack(alignment: .topLeading) {
            ForEach(yAxisLabelValues, id: \.self) { value in
                Text(CompactNumberFormat.percentString(value))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    // One line, shrunk rather than wrapped: a wrapped label left its line.
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    .frame(width: labelWidth)
                    .position(x: labelWidth / 2, y: yLabelCenter(value))
            }
        }
        .frame(width: labelWidth, height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - X-Axis Labels

    // Each label sits at the SAME column centre (`xCenter`) the chart's marks use —
    // not in a flush-divided HStack, whose columns never matched the plot's scale.
    private func xAxisLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(dataPoints.enumerated()), id: \.element.id) { index, dataPoint in
                Text(dataPoint.period)
                    .font(.system(size: 11))
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: xAxisHeight / 2)
            }
        }
        .frame(width: plotWidth, height: xAxisHeight, alignment: .leading)
    }

    // MARK: - Selection Helper

    private func updateSelection(at location: CGPoint, chartWidth: CGFloat) {
        // Nearest column CENTRE under the index scale (with its edge pad) — the flush
        // `ChartDomain.columnIndex` split the width into equal columns, which is not
        // where the marks are. Validates before converting: a GeometryReader reporting
        // width 0 mid-transition would otherwise make `Int(.infinity)` trap.
        guard let index = nearestIndex(atX: location.x, plotWidth: chartWidth) else { return }
        selectedDataPoint = dataPoints[index]
    }
}

// MARK: - Profit Power Tooltip View

struct ProfitPowerTooltipView: View {
    let dataPoint: ProfitPowerDataPoint
    var peerWord: String = "Sector"

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Period header
            Text(dataPoint.period)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .padding(.bottom, AppSpacing.xxs)

            // All margin values
            tooltipRow(
                title: "Gross Margin",
                value: dataPoint.grossMargin,
                color: AppColors.profitGrossMargin
            )

            tooltipRow(
                title: "Operating Margin",
                value: dataPoint.operatingMargin,
                color: AppColors.profitOperatingMargin
            )

            tooltipRow(
                title: "FCF Margin",
                value: dataPoint.fcfMargin,
                color: AppColors.profitFCFMargin
            )

            tooltipRow(
                title: "Net Margin",
                value: dataPoint.netMargin,
                color: AppColors.profitNetMargin
            )

            tooltipRow(
                title: "\(peerWord) Avg",
                value: dataPoint.sectorAverageNetMargin,
                color: AppColors.profitSectorAverage
            )
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill()
                .shadow(color: AppColors.shadowAmbient, radius: 8, x: 0, y: 4)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }

    /// `value == nil` means the margin genuinely isn't reported for this period
    /// (a bank has no gross profit; a thin industry has no sector median). Show
    /// an em dash rather than "0.0%", which reads as a real measurement.
    private func tooltipRow(title: String, value: Double?, color: Color) -> some View {
        HStack(spacing: AppSpacing.sm) {
            // Color indicator
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            // Title
            Text(title)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            // Value
            Text(value.map { String(format: "%.1f%%", $0) } ?? "—")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(value == nil ? AppColors.textMuted : AppColors.textPrimary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            ProfitPowerChartView(
                dataPoints: ProfitPowerSectionData.sampleData.annualData,
                selectedDataPoint: .constant(nil)
            )
            .padding()
        }
    }
}

#Preview("Outlier pinned, gap, zero rule") {
    // An early-revenue shape: FY2021's ~$1M of revenue puts net at −40,000% and FCF at
    // −52,000%. The robust axis keeps the later years readable, pins both outliers to the
    // bottom edge with their true values, breaks the FCF line at the missing 2023, and
    // draws the 0% rule across a mixed-sign axis. Illustrative values, not a real ticker.
    let periods = ["2021", "2022", "2023", "2024", "2025"]
    let gross: [Double?] = [-120, 18, 31, 38, 41]
    let operating: [Double?] = [-39000, -64, -22, -6, 4]
    let fcf: [Double?] = [-52000, -71, nil, -9, 6]
    let net: [Double?] = [-40000, -58, -25, -8, 3]
    let points = periods.indices.map { i in
        ProfitPowerDataPoint(
            period: periods[i],
            grossMargin: gross[i],
            operatingMargin: operating[i],
            fcfMargin: fcf[i],
            netMargin: net[i],
            sectorAverageNetMargin: 7.5
        )
    }
    return ZStack {
        AppColors.background
            .ignoresSafeArea()

        ProfitPowerChartView(dataPoints: points, selectedDataPoint: .constant(nil))
            .padding()
    }
}

#Preview("Above-scale pinned, top edge") {
    // A one-off asset-sale gain booked against a near-zero-revenue year puts 2023's
    // operating and net margins at +1,900% / +2,400%. Both are pinned to the TOP edge;
    // their up-arrows must be drawn whole (not clipped by the ScrollView) with the true
    // values stacked below them. Illustrative values, not a real ticker.
    let periods = ["2021", "2022", "2023", "2024", "2025"]
    let gross: [Double?] = [62, 64, 66, 65, 67]
    let operating: [Double?] = [18, 20, 1900, 21, 22]
    let fcf: [Double?] = [12, 13, -40, 15, 16]
    let net: [Double?] = [14, 15, 2400, 16, 17]
    let points = periods.indices.map { i in
        ProfitPowerDataPoint(
            period: periods[i],
            grossMargin: gross[i],
            operatingMargin: operating[i],
            fcfMargin: fcf[i],
            netMargin: net[i],
            sectorAverageNetMargin: 11.0
        )
    }
    return ZStack {
        AppColors.background
            .ignoresSafeArea()

        ProfitPowerChartView(dataPoints: points, selectedDataPoint: .constant(nil))
            .padding()
    }
}

#Preview("Degraded build, empty series") {
    // An upstream leg failed and emptied this series: the empty state says so instead of
    // calling it a fact about the company.
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ProfitPowerChartView(dataPoints: [], selectedDataPoint: .constant(nil), isDegraded: true)
            .padding()
    }
}
