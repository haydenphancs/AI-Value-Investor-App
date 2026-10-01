//
//  ProfitabilityChartView.swift
//  ios
//
//  Molecule: per-metric 2-LINE chart for the report's Profitability drill-down —
//  a YELLOW company line + a GRAY DASHED sector-average line on a single shared
//  percent scale (NO bars). Mirrors GrowthChartView's manual y-axis column +
//  horizontally-scrollable, edge-to-edge plot (`.plotDimension(padding:)`) so it
//  reads identically to the Growth drill-down — but both lines live on one REAL
//  percent y-axis (no normalized overlay), so the numbers are read straight off it.
//

import SwiftUI
import Charts

struct ProfitabilityChartView: View {
    let points: [ProfitabilityChartPoint]
    /// nil → no good/bad band drawn. All profitability metrics are higher-is-better,
    /// so the sheet passes `true`; nil keeps legacy/preview callers band-free.
    var higherIsBetter: Bool? = nil

    private let chartHeight: CGFloat = 220
    private let visibleColumnCount: CGFloat = 6
    /// Room reserved at each plot edge for the widest centered edge label
    /// ("-44.8%", "Q1 '24"). In points (font-fixed), so it never clips.
    private let edgeLabelPad: CGFloat = 24
    private let labelFontSize: CGFloat = 11

    private var needsScroll: Bool { points.count > Int(visibleColumnCount) }

    private var companyValues: [Double] { points.compactMap { $0.company } }
    private var sectorValues: [Double] { points.compactMap { $0.sector } }
    private var allValues: [Double] { companyValues + sectorValues }

    /// Green/red good-vs-benchmark band, built from the SAME clamped values the lines
    /// draw so its edges coincide with them. (It used raw values under a comment calling
    /// the percent domain "well-behaved" — it is not: a near-zero-revenue year or a
    /// near-zero-equity ROE reaches thousands of percent.) Clamping is monotonic, so the
    /// company-vs-benchmark sign, and with it the colour, is preserved wherever the band
    /// is visible. Empty when polarity is unknown or no sector value exists at any period.
    private var bandSegments: [DirectionalBandSegment] {
        guard let hib = higherIsBetter else { return [] }
        return directionalBandSegments(
            company: points.map { $0.company.flatMap(drawnValue) },
            sector: points.map { $0.sector.flatMap(drawnValue) },
            higherIsBetter: hib
        )
    }

    /// Single percent domain shared by BOTH lines (a REAL axis, unlike Growth's
    /// normalized overlay — here both series are percentages). Sign-aware + anchors
    /// 0 so a negative margin (FCF / loss-maker Net) renders below a visible zero
    /// line, and an all-negative series never inverts.
    ///
    /// The old min/max domain (×1.12 headroom on each signed side), built from
    /// `axisValues` — so ordinary data keeps EXACTLY the old axis. Only an extreme
    /// outlier (a tiny-revenue year at −8,524% net, a near-zero-equity ROE) is left out:
    /// it is drawn CLAMPED to the edge with an outward arrow (`offScaleMarks`), and the
    /// value rows under the plot keep printing the TRUE number for every column.
    private var yDomain: ClosedRange<Double> {
        let values = axisValues
        var lo = Swift.min(values.min() ?? 0, 0)
        var hi = Swift.max(values.max() ?? 1, 0)
        if hi > 0 { hi *= 1.12 }
        if lo < 0 { lo *= 1.12 }
        if lo == hi { hi = lo + 1 }
        return lo...hi
    }

    /// |value| (%) at or below which a value ALWAYS sets the axis. Beyond ±100% a margin
    /// only comes from a near-zero revenue period, and an ROE / ROA from a near-zero
    /// equity / asset base; an ordinary year never reaches it.
    private static let extremeValuePct: Double = 100

    /// The values that SET the axis: every finite value except one that is BOTH extreme
    /// in size (|v| > `extremeValuePct`) AND outside the pooled robust fence
    /// (`ChartDomain.robust`). The fence alone pins ordinary values whenever the company
    /// and benchmark lines sit in separate clusters; the size test alone would pin a
    /// loss-maker whose whole history is below −100%.
    private var axisValues: [Double] {
        let finite = allValues.filter { $0.isFinite }
        let fence = ChartDomain.robust(
            finite, includeZero: true, headroomFraction: 0, fallback: 0...1
        )
        return finite.filter { abs($0) <= Self.extremeValuePct || fence.contains($0) }
    }

    /// The y a value is DRAWN at: clamped into `yDomain`; nil for a non-finite value
    /// (no vertex rather than a NaN handed to the chart).
    private func drawnValue(_ value: Double) -> Double? {
        value.isFinite ? ChartDomain.clamp(value, to: yDomain) : nil
    }

    private func isOffScale(_ value: Double) -> Bool {
        ChartDomain.isOffScale(value, in: yDomain)
    }

    // Grid lines: the zero baseline plus interior lines across the sign-aware domain.
    private var gridValues: [Double] {
        let hi = yDomain.upperBound, lo = yDomain.lowerBound
        var vals: [Double] = [0]
        if hi > 0 { vals += [hi / 3, 2 * hi / 3] }
        if lo < 0 { vals += [lo / 3, 2 * lo / 3] }
        return vals
    }

    /// Contiguous non-nil runs → a line BREAKS at undefined periods instead of
    /// bridging them with a fabricated straight segment.
    private func segments(
        _ valueFor: @escaping (ProfitabilityChartPoint) -> Double?
    ) -> [(index: Int, value: Double, seg: Int)] {
        var out: [(index: Int, value: Double, seg: Int)] = []
        var seg = 0
        var prevWasNil = true
        for (i, p) in points.enumerated() {
            guard let v = valueFor(p), v.isFinite else { prevWasNil = true; continue }
            if prevWasNil { seg += 1; prevWasNil = false }
            out.append((index: i, value: v, seg: seg))
        }
        return out
    }
    private var companySegments: [(index: Int, value: Double, seg: Int)] { segments { $0.company } }
    private var sectorSegments: [(index: Int, value: Double, seg: Int)] { segments { $0.sector } }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: y-axis labels, sized to their own width (hug the edge).
            VStack(alignment: .leading, spacing: 0) {
                yAxisLabels
                Spacer()
                    .frame(height: 20 + AppSpacing.md + (20 + AppSpacing.sm) * 2)
            }
            .fixedSize(horizontal: true, vertical: false)

            // Right column: scrollable plot.
            GeometryReader { geometry in
                let visibleWidth = Swift.max(geometry.size.width, 1)
                let count = points.count
                let plotWidth = needsScroll
                    ? CGFloat(count) * (visibleWidth / visibleColumnCount)
                    : visibleWidth

                ScrollView(.horizontal, showsIndicators: needsScroll) {
                    VStack(alignment: .leading, spacing: 0) {
                        chartArea(plotWidth: plotWidth)
                            .frame(width: plotWidth, height: chartHeight)

                        xAxisLabels(plotWidth: plotWidth)
                            .padding(.top, AppSpacing.md)

                        companyLabels(plotWidth: plotWidth)
                            .padding(.top, AppSpacing.sm)

                        sectorLabels(plotWidth: plotWidth)
                            .padding(.top, AppSpacing.sm)
                    }
                    .frame(width: plotWidth, alignment: .leading)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + 20 + AppSpacing.md + (20 + AppSpacing.sm) * 2 + (needsScroll ? AppSpacing.md : 0))
        }
    }

    // MARK: - X positioning (edge-to-edge, matches GrowthChartView)

    private func xDomain() -> ClosedRange<Double> {
        let n = points.count
        guard n > 1 else { return -0.5 ... 0.5 }
        return 0.0 ... Double(n - 1)
    }
    private func xCenter(_ index: Int, plotWidth: CGFloat) -> CGFloat {
        let n = points.count
        guard n > 1 else { return plotWidth / 2 }
        let usable = Swift.max(plotWidth - 2 * edgeLabelPad, 1)
        return edgeLabelPad + CGFloat(index) / CGFloat(n - 1) * usable
    }

    // MARK: - Chart Area

    private func chartArea(plotWidth: CGFloat) -> some View {
        Chart {
            // Horizontal grid lines (behind everything), incl. the zero baseline.
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // Directional good/bad band: green where the company is on this metric's
            // favorable side of the benchmark, red on the bad side — split at
            // crossovers so the color flips exactly where the two lines meet.
            ForEach(bandSegments) { seg in
                ForEach(Array(seg.vertices.enumerated()), id: \.offset) { _, v in
                    AreaMark(
                        x: .value("i", v.x),
                        yStart: .value("lo", v.lower),
                        yEnd: .value("hi", v.upper),
                        series: .value("band", seg.id)
                    )
                    .foregroundStyle((seg.isGood ? AppColors.bullish : AppColors.bearish).opacity(0.22))
                    .interpolationMethod(.linear)
                }
            }

            // Sector dashed line first so the company line sits on top. Every mark draws
            // the CLAMPED value, so nothing leaves the plot.
            ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("i", Double(item.index)),
                    y: .value("Sector", ChartDomain.clamp(item.value, to: yDomain)),
                    series: .value("Series", "Sector-\(item.seg)")
                )
                .foregroundStyle(AppColors.growthSectorGray)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 4]))
                .interpolationMethod(.linear)
            }
            ForEach(Array(sectorSegments.filter { !isOffScale($0.value) }.enumerated()), id: \.offset) { _, item in
                PointMark(x: .value("i", Double(item.index)), y: .value("Sector", item.value))
                    .foregroundStyle(AppColors.growthSectorGray)
                    .symbolSize(28)
            }

            // Company yellow solid line.
            ForEach(Array(companySegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("i", Double(item.index)),
                    y: .value("Company", ChartDomain.clamp(item.value, to: yDomain)),
                    series: .value("Series", "Company-\(item.seg)")
                )
                .foregroundStyle(AppColors.growthYoYYellow)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.linear)
            }
            ForEach(Array(companySegments.filter { !isOffScale($0.value) }.enumerated()), id: \.offset) { _, item in
                PointMark(x: .value("i", Double(item.index)), y: .value("Company", item.value))
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .symbolSize(45)
            }

            // Values beyond the robust domain: an outward arrow pinned to the edge.
            offScaleMarks
        }
        .chartXScale(domain: xDomain(), range: .plotDimension(padding: edgeLabelPad))
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: yDomain)
        // Not `.clipped()`: every mark is drawn at a CLAMPED value, so nothing leaves the
        // plot; clamping is the overflow guard. The off-scale arrows sit just inside the
        // edge (`offScaleGlyphInset`) because the enclosing ScrollView does clip there.
        .chartPlotStyle { $0.background(Color.clear) }
    }

    /// How far a pinned arrow is shifted inside the plot (about half its 9pt glyph), so it
    /// is drawn whole. Not `.scrollClipDisabled()`: a scrolled chart would then draw over
    /// the fixed y-axis column.
    private let offScaleGlyphInset: CGFloat = 5

    /// One pinned value: its column, the edge it is drawn on and its series colour.
    private struct OffScaleMarker: Identifiable {
        let id: String
        let index: Int
        let edgeValue: Double
        let actual: Double
        let isAbove: Bool
        let isCompany: Bool

        var accessibilityName: String {
            (isCompany ? "Company" : "Benchmark") + ", off the chart scale"
        }
    }

    private var offScaleMarkers: [OffScaleMarker] {
        let domain = yDomain
        func markers(_ items: [(index: Int, value: Double, seg: Int)], company: Bool) -> [OffScaleMarker] {
            items.filter { isOffScale($0.value) }.map { item in
                OffScaleMarker(
                    id: "\(company ? "c" : "s")-\(item.index)",
                    index: item.index,
                    edgeValue: ChartDomain.clamp(item.value, to: domain),
                    actual: item.value,
                    isAbove: item.value > domain.upperBound,
                    isCompany: company
                )
            }
        }
        return markers(sectorSegments, company: false) + markers(companySegments, company: true)
    }

    /// The pinned value as an outward arrow ON the plot edge (owner decision, 2026-09-30:
    /// cap at the edge and mark it). Its real value is printed by the company / sector
    /// value rows directly under the column, so no second label is drawn in the plot;
    /// the mark carries it for VoiceOver.
    @ChartContentBuilder
    private var offScaleMarks: some ChartContent {
        ForEach(offScaleMarkers) { marker in
            PointMark(
                x: .value("i", Double(marker.index)),
                y: .value("Edge", marker.edgeValue)
            )
            .symbol {
                // Chrome, not content: hidden from VoiceOver (the mark carries the label),
                // so the 3:1 series token is the right floor for the glyph.
                // Shifted INSIDE the plot so its tip sits on the edge: the chart's top is
                // flush with the horizontal ScrollView, which clips, so an arrow centred on
                // the edge lost its upper half.
                Image(systemName: marker.isAbove ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                    .accessibilityHidden(true)
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(marker.isCompany ? AppColors.growthYoYYellow : AppColors.growthSectorGray)
                    .offset(y: marker.isAbove ? offScaleGlyphInset : -offScaleGlyphInset)
            }
            .accessibilityLabel(marker.accessibilityName)
            .accessibilityValue(fmtPct(marker.actual))
        }
    }

    // MARK: - Y-axis labels (percent)

    private var yAxisLabels: some View {
        let hi = yDomain.upperBound
        let lo = yDomain.lowerBound
        let span = hi - lo
        return VStack(alignment: .leading) {
            Text(fmtPctAxis(hi)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(fmtPctAxis(hi - span / 3)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(fmtPctAxis(lo + span / 3)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(lo < 0 ? fmtPctAxis(lo) : "0%").font(AppTypography.caption).foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Manual label rows (same xCenter grid as the lines)

    private func xAxisLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(points.enumerated()), id: \.offset) { index, p in
                Text(p.period)
                    .font(.system(size: labelFontSize, weight: .regular))
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1).fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: 10)
            }
        }
        .frame(width: plotWidth, height: 20, alignment: .leading)
    }

    private func companyLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(points.enumerated()), id: \.offset) { index, p in
                Text(p.company.map { fmtPct($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .semibold))
                    // Text-safe sibling of the series token (3.57:1 on a light card).
                    .foregroundColor(AppColors.accentYellow)
                    .lineLimit(1).fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: 10)
            }
        }
        .frame(width: plotWidth, height: 20, alignment: .leading)
    }

    private func sectorLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(points.enumerated()), id: \.offset) { index, p in
                Text(p.sector.map { fmtPct($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .regular))
                    // Text-safe sibling of `growthSectorGray` (3.78:1 light card).
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1).fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: 10)
            }
        }
        .frame(width: plotWidth, height: 20, alignment: .leading)
    }

    // MARK: - Formatters

    private func fmtPct(_ v: Double) -> String {
        abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.1f%%", v)
    }
    /// Whole percent below ±1,000%, compact beyond ("-3.1k%") so a wide axis stays short.
    private func fmtPctAxis(_ v: Double) -> String {
        CompactNumberFormat.percentString(v)
    }
}

#Preview {
    // Gross Margin (higher-is-better): company ABOVE industry early (green), then
    // dips BELOW it in 2026 (red) — the band flips at the crossover, like the report.
    let periods = ["2021", "2022", "2023", "2024", "2025", "2026"]
    let comp = [80.6, 79.1, 72.8, 71.4, 70.5, 65.2]
    let ind = [68.9, 68.1, 69.7, 71.3, 71.4, 74.1]
    let points = (0..<periods.count).map {
        ProfitabilityChartPoint(period: periods[$0], company: comp[$0], sector: ind[$0])
    }
    // An early-revenue Net Margin: 2021's tiny revenue puts it at −8,524%. The robust
    // axis keeps 2022-2026 readable and pins 2021 to the bottom edge with an arrow; the
    // value row below still prints the true −8524%. Illustrative values.
    let outlierNet: [Double] = [-8523.6, -407.2, -188.4, -95.5, -60.1, -24.1]
    let outlierPoints = (0..<periods.count).map {
        ProfitabilityChartPoint(period: periods[$0], company: outlierNet[$0], sector: -12.0)
    }
    return ScrollView {
        VStack(spacing: 24) {
            ProfitabilityChartView(points: points, higherIsBetter: true)   // green → red at 2026
            ProfitabilityChartView(points: points)                         // no band (lines only)
            ProfitabilityChartView(points: outlierPoints, higherIsBetter: true)  // pinned outlier
        }
        .padding()
    }
    .background(AppColors.background)
}
