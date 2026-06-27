//
//  MetricHistoryLineChart.swift
//  ios
//
//  Molecule: per-metric 2-LINE chart for the report's VALUATION drill-down — a
//  YELLOW company line + a GRAY DASHED sector/industry-average line (NO bars),
//  with company + benchmark value rows beneath each period. Mirrors
//  ProfitabilityChartView's layout 1:1 so Valuation reads identically to the
//  Profitability drill-down, BUT it is UNIT-AWARE (multiples "x", "percent",
//  "score") and reuses MetricHistoryChart's OUTLIER-ROBUST y-domain so a single
//  P/E spike (e.g. 5000×) pins to the top edge instead of flattening every other
//  point onto a sliver. Renders frozen report data; no network call.
//
//  Data: company `points` drive the x-categories (oldest→newest, INCLUDING
//  periods whose value is nil — a negative-earnings P/E, a negative-FCF P/FCF —
//  so the timeline always ends on the latest period); `sector` is looked up by
//  period label (the backend already aligns them). A nil company value BREAKS the
//  yellow line and the bottom label reads "—"; a nil sector value breaks the
//  dashed line there. The benchmark line + its value row only render when a
//  sector series actually exists (some valuation metrics have no peer median).
//

import SwiftUI
import Charts

struct MetricHistoryLineChart: View {
    let points: [MetricHistoryPoint]         // company, oldest→newest
    var sector: [MetricHistoryPoint]? = nil  // sector-average overlay (period-aligned)
    let unit: String?                        // "percent" | "x" | "score"
    /// nil → no good/bad band drawn (lines only). true = above-benchmark is good,
    /// false = below-benchmark is good. See DeepDiveMetric.higherIsBetter(forHistoryKey:).
    var higherIsBetter: Bool? = nil
    /// Absolute-threshold zones (e.g. Altman Z bankruptcy bands) drawn INSTEAD of
    /// the industry band — for metrics with no benchmark line. nil → normal mode.
    var thresholds: MetricThresholdZones? = nil

    private let chartHeight: CGFloat = 220
    private let visibleColumnCount: CGFloat = 6
    /// Room reserved at each plot edge for the widest centered edge label, in
    /// points (font-fixed), so the newest/oldest label never clips.
    private let edgeLabelPad: CGFloat = 24
    private let labelFontSize: CGFloat = 11

    // MARK: - Aligned per-period rows (company drives the x-categories)

    private struct Row {
        let period: String
        let company: Double?
        let sector: Double?
    }

    /// Merge company + sector into one row per company period (sector joined by
    /// label, matching the backend's alignment).
    private var rows: [Row] {
        var sectorByPeriod: [String: Double] = [:]
        for p in (sector ?? []) {
            if let v = p.value { sectorByPeriod[p.period] = v }
        }
        return points.map { Row(period: $0.period, company: $0.value, sector: sectorByPeriod[$0.period]) }
    }

    private var companyValues: [Double] { rows.compactMap { $0.company } }
    private var sectorValues: [Double] { rows.compactMap { $0.sector } }
    private var allValues: [Double] { companyValues + sectorValues }

    /// Only draw the benchmark line + its value row when a sector series exists.
    private var showSector: Bool { !sectorValues.isEmpty }

    private var needsScroll: Bool { rows.count > Int(visibleColumnCount) }

    /// Green/red good-vs-benchmark band, built from the SAME clamped values used to
    /// draw the lines so its edges coincide with them. Empty when polarity is
    /// unknown or no benchmark line exists.
    private var bandSegments: [DirectionalBandSegment] {
        guard let hib = higherIsBetter, showSector else { return [] }
        let comp = rows.map { $0.company.map(clamped) }
        let sect = rows.map { $0.sector.map(clamped) }
        return directionalBandSegments(company: comp, sector: sect, higherIsBetter: hib)
    }

    /// Height of the label block beneath the plot: the x-axis row, the company
    /// value row, and (when present) the sector value row — each with its gap.
    private var belowChartHeight: CGFloat {
        let xRow = 20 + AppSpacing.md
        let valueRow = 20 + AppSpacing.sm
        return xRow + valueRow + (showSector ? valueRow : 0)
    }

    // MARK: - Contiguous non-nil runs → a line BREAKS at undefined periods

    private func segments(_ valueFor: (Row) -> Double?) -> [(index: Int, value: Double, seg: Int)] {
        var out: [(index: Int, value: Double, seg: Int)] = []
        var seg = 0
        var prevWasNil = true
        for (i, r) in rows.enumerated() {
            guard let v = valueFor(r) else { prevWasNil = true; continue }
            if prevWasNil { seg += 1; prevWasNil = false }
            out.append((index: i, value: v, seg: seg))
        }
        return out
    }
    private var companySegments: [(index: Int, value: Double, seg: Int)] { segments { $0.company } }
    private var sectorSegments: [(index: Int, value: Double, seg: Int)] { segments { $0.sector } }

    var body: some View {
        if companyValues.count < 2 {
            Text("Not enough history to chart.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, minHeight: 180)
        } else {
            chartBody
        }
    }

    private var chartBody: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left column: y-axis labels, sized to their own width (hug the edge).
            VStack(alignment: .leading, spacing: 0) {
                yAxisLabels
                Spacer().frame(height: belowChartHeight)
            }
            .fixedSize(horizontal: true, vertical: false)

            // Right column: scrollable plot + manual label rows.
            GeometryReader { geometry in
                let visibleWidth = Swift.max(geometry.size.width, 1)
                let count = rows.count
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

                        if showSector {
                            sectorLabels(plotWidth: plotWidth)
                                .padding(.top, AppSpacing.sm)
                        }
                    }
                    .frame(width: plotWidth, alignment: .leading)
                    .padding(.bottom, needsScroll ? AppSpacing.md : 0)
                }
                .defaultScrollAnchor(.trailing)
            }
            .frame(height: chartHeight + belowChartHeight + (needsScroll ? AppSpacing.md : 0))
        }
    }

    // MARK: - X positioning (edge-to-edge, matches ProfitabilityChartView)

    private func xDomain() -> ClosedRange<Double> {
        let n = rows.count
        guard n > 1 else { return -0.5 ... 0.5 }
        return 0.0 ... Double(n - 1)
    }
    private func xCenter(_ index: Int, plotWidth: CGFloat) -> CGFloat {
        let n = rows.count
        guard n > 1 else { return plotWidth / 2 }
        let usable = Swift.max(plotWidth - 2 * edgeLabelPad, 1)
        return edgeLabelPad + CGFloat(index) / CGFloat(n - 1) * usable
    }

    // MARK: - Chart Area

    private func chartArea(plotWidth: CGFloat) -> some View {
        Chart {
            if let tz = thresholds {
                // Absolute-threshold zones (Altman Z): colored bands divided by the
                // cutoff lines — red distress / yellow grey / green safe.
                ForEach(zoneRects(tz)) { z in
                    RectangleMark(yStart: .value("lo", z.lo), yEnd: .value("hi", z.hi))
                        .foregroundStyle(z.color.opacity(0.14))
                }
                ForEach(tz.thresholds, id: \.self) { t in
                    RuleMark(y: .value("threshold", t))
                        .foregroundStyle(AppColors.textSecondary.opacity(0.45))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                }
            } else {
                // Horizontal grid lines (behind everything), incl. the zero baseline.
                ForEach(gridValues, id: \.self) { value in
                    RuleMark(y: .value("Grid", value))
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 0.5))
                }

                // Directional good/bad band: green where the company is on this
                // metric's favorable side of the benchmark, red on the bad side —
                // split at crossovers so the color flips where the two lines meet.
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
            }

            // Sector dashed line first so the company line sits on top.
            if showSector {
                ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                    LineMark(
                        x: .value("i", Double(item.index)),
                        y: .value("Sector", clamped(item.value)),
                        series: .value("Series", "Sector-\(item.seg)")
                    )
                    .foregroundStyle(AppColors.growthSectorGray)
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 4]))
                    .interpolationMethod(.linear)
                }
                ForEach(Array(sectorSegments.enumerated()), id: \.offset) { _, item in
                    PointMark(x: .value("i", Double(item.index)), y: .value("Sector", clamped(item.value)))
                        .foregroundStyle(AppColors.growthSectorGray)
                        .symbolSize(28)
                }
            }

            // Company yellow solid line.
            ForEach(Array(companySegments.enumerated()), id: \.offset) { _, item in
                LineMark(
                    x: .value("i", Double(item.index)),
                    y: .value("Company", clamped(item.value)),
                    series: .value("Series", "Company-\(item.seg)")
                )
                .foregroundStyle(AppColors.growthYoYYellow)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.linear)
            }
            ForEach(Array(companySegments.enumerated()), id: \.offset) { _, item in
                PointMark(x: .value("i", Double(item.index)), y: .value("Company", clamped(item.value)))
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .symbolSize(45)
            }
        }
        .chartXScale(domain: xDomain(), range: .plotDimension(padding: edgeLabelPad))
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: yDomain)
        .chartPlotStyle { $0.background(Color.clear) }
    }

    // MARK: - Y-axis labels (unit-aware)

    private var yAxisLabels: some View {
        let hi = yDomain.upperBound
        let lo = yDomain.lowerBound
        let span = hi - lo
        return VStack(alignment: .leading) {
            Text(fmtAxis(hi)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(fmtAxis(hi - span / 3)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(fmtAxis(lo + span / 3)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
            Spacer()
            Text(lo < 0 ? fmtAxis(lo) : fmtAxis(0)).font(AppTypography.caption).foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Manual label rows (same xCenter grid as the lines)

    private func xAxisLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, r in
                Text(r.period)
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
            ForEach(Array(rows.enumerated()), id: \.offset) { index, r in
                Text(r.company.map { fmtValue($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .semibold))
                    .foregroundColor(AppColors.growthYoYYellow)
                    .lineLimit(1).fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: 10)
            }
        }
        .frame(width: plotWidth, height: 20, alignment: .leading)
    }

    private func sectorLabels(plotWidth: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, r in
                Text(r.sector.map { fmtValue($0) } ?? "—")
                    .font(.system(size: labelFontSize, weight: .regular))
                    .foregroundColor(AppColors.growthSectorGray)
                    .lineLimit(1).fixedSize()
                    .position(x: xCenter(index, plotWidth: plotWidth), y: 10)
            }
        }
        .frame(width: plotWidth, height: 20, alignment: .leading)
    }

    // MARK: - Grid lines

    private var gridValues: [Double] {
        let hi = yDomain.upperBound, lo = yDomain.lowerBound
        var vals: [Double] = [0]
        if hi > 0 { vals += [hi / 3, 2 * hi / 3] }
        if lo < 0 { vals += [lo / 3, 2 * lo / 3] }
        return vals
    }

    // MARK: - Outlier-robust y-domain (ported from MetricHistoryChart)

    /// ALWAYS includes zero (so the baseline is visible even for an all-negative
    /// series) and clamps only GENUINELY extreme points to the edge (a P/E of
    /// 5000×, an interest-coverage of −5000×). Modest negatives render at TRUE
    /// height. Considers both lines so an outlier in EITHER pins, not escapes.
    private var yDomain: ClosedRange<Double> {
        if let td = thresholdDomain { return td }
        let vals = allValues.sorted()
        guard let lo = vals.first, let hi = vals.last else { return 0...1 }
        let n = vals.count
        let q1 = vals[n / 4]
        // Cap q3 BELOW the absolute max for n >= 3 so a single extreme value can't
        // BECOME q3 and collapse the upper fence onto itself.
        let q3 = vals[n >= 3 ? Swift.min((n * 3) / 4, n - 2) : (n - 1)]
        let iqr = Swift.max(q3 - q1, abs(q3) * 0.1, 1)
        let scale = Swift.max(abs(q3), abs(q1), iqr)
        let fenceHi = Swift.min(hi, q3 + 1.5 * iqr)
        let fenceLo = Swift.max(lo, -3.0 * scale)
        var top = Swift.max(fenceHi, 0)
        var bottom = Swift.min(fenceLo, 0)
        if top == bottom { top = bottom + 1 }
        let span = top - bottom
        // Pad outward (only away from zero) so the line doesn't touch the frame.
        top += (top > 0) ? span * 0.08 : 0
        bottom -= (bottom < 0) ? span * 0.08 : 0
        return bottom...top
    }

    // MARK: - Threshold zones (absolute cutoffs, e.g. Altman Z)

    /// When zones are shown, the domain must SPAN the cutoffs (so every zone +
    /// boundary line is visible) regardless of the data range.
    private var thresholdDomain: ClosedRange<Double>? {
        guard let tz = thresholds,
              let firstT = tz.thresholds.first, let lastT = tz.thresholds.last else { return nil }
        let dMin = companyValues.min() ?? firstT
        let dMax = companyValues.max() ?? lastT
        var lo = Swift.min(0, dMin, firstT)
        var hi = Swift.max(dMax, lastT)
        if hi <= lo { hi = lo + 1 }
        let pad = (hi - lo) * 0.10
        hi += pad
        if lo < 0 { lo -= pad }   // pad below only when negative; keep the 0 anchor otherwise
        return lo...hi
    }

    private struct ZoneRect: Identifiable { let id: Int; let lo: Double; let hi: Double; let color: Color }

    /// The colored bands between consecutive thresholds, clamped to the domain.
    private func zoneRects(_ tz: MetricThresholdZones) -> [ZoneRect] {
        let bounds = [yDomain.lowerBound] + tz.thresholds + [yDomain.upperBound]
        var rects: [ZoneRect] = []
        for i in 0..<(bounds.count - 1) where i < tz.zoneColors.count {
            let lo = Swift.max(bounds[i], yDomain.lowerBound)
            let hi = Swift.min(bounds[i + 1], yDomain.upperBound)
            if hi > lo { rects.append(ZoneRect(id: i, lo: lo, hi: hi, color: tz.zoneColors[i])) }
        }
        return rects
    }

    /// Clamp a plotted value into the robust domain (the bottom labels still show
    /// the TRUE value, so an outlier reads "5000.0x" even though its vertex pins).
    private func clamped(_ v: Double) -> Double {
        Swift.min(Swift.max(v, yDomain.lowerBound), yDomain.upperBound)
    }

    // MARK: - Unit-aware formatters

    /// Compact value for the bottom rows — drops decimals when large.
    private func fmtValue(_ v: Double) -> String {
        switch unit {
        case "percent": return abs(v) >= 100 ? String(format: "%.0f%%", v) : String(format: "%.1f%%", v)
        case "score":   return String(format: "%.1f", v)
        default:        return abs(v) >= 100 ? String(format: "%.0fx", v) : String(format: "%.1fx", v)  // "x" / nil
        }
    }
    /// Whole-number axis ticks.
    private func fmtAxis(_ v: Double) -> String {
        switch unit {
        case "percent": return String(format: "%.0f%%", v)
        case "score":   return String(format: "%.0f", v)
        default:        return String(format: "%.0fx", v)  // "x" / nil
        }
    }
}

#Preview {
    // P/E (lower-is-better): company starts ABOVE industry (red / expensive), then
    // crosses BELOW it (green / cheap) — the band flips color at the crossover.
    let company: [MetricHistoryPoint] = [38.0, 36, 31, 26, 22, 19].enumerated().map {
        MetricHistoryPoint(period: String(2021 + $0.offset), value: $0.element)
    }
    let industry: [MetricHistoryPoint] = [27.0, 27.5, 28, 28.5, 29, 29.5].enumerated().map {
        MetricHistoryPoint(period: String(2021 + $0.offset), value: $0.element)
    }
    // Altman Z-Score (absolute thresholds): red/yellow/green zones, no benchmark.
    let zscore: [MetricHistoryPoint] = [1.9, 1.5, 1.7, 1.9, 2.4, 2.5].enumerated().map {
        MetricHistoryPoint(period: String(2021 + $0.offset), value: $0.element)
    }
    return ScrollView {
        VStack(spacing: 24) {
            // lower-is-better → red while expensive, green once cheaper
            MetricHistoryLineChart(points: company, sector: industry, unit: "x", higherIsBetter: false)
            // same data, flipped polarity → green→red (illustrates direction matters)
            MetricHistoryLineChart(points: company, sector: industry, unit: "x", higherIsBetter: true)
            // no benchmark → no band, just the company line
            MetricHistoryLineChart(points: company, sector: nil, unit: "x")
            // Altman Z: bankruptcy zones, line sits in the grey band
            MetricHistoryLineChart(points: zscore, sector: nil, unit: "score", thresholds: .altmanZ)
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
