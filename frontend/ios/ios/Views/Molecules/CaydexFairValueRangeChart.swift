//
//  CaydexFairValueRangeChart.swift
//  ios
//
//  Molecule: the price line over its window, the price as a dashed line across it, and the
//  Caydex Fair Value Estimate's RANGE as a pole at the right edge. One right-hand column labels
//  everything on the chart's scale — "Price $341.07", "High $266.46", "Estimate $213.29",
//  "Low $176.20" — each on ONE line, all starting at the same x, each level with the mark it
//  names. Used by the report's "Valuation & Institutions" section and the Analysis tab's
//  Valuation card, so both draw the estimate the same way.
//
//  ⚠️ Rules this view carries (documents/research/dcf-methodology-v1.md §5, pinned by
//  backend/tests/test_ios_fair_value.py):
//  • the pole reads only `CaydexFairValue.bounds`, so an estimate is never drawn without its
//    range;
//  • neutral ink only — no gain/loss/bullish/bearish colour on anything that marks the
//    estimate, and no signed percent on a label (the gap is stated once, in the header);
//  • the range is drawn at the right edge only. A band across the whole plot would read as
//    "a fair value over the past two years" — the estimate is as of one day.
//
//  2026-09-26 (owner: "the chart and prices don't fit or align at all"): the first version
//  reused the old analyst chart's two-line badges (label OVER price) in a 60pt gutter. The
//  column came out ragged, "Low" sat directly under the estimate's price and read as its
//  label, and the dashed price line ended at the pole with no number on it. Now the column is
//  single-line, left-aligned and wide enough, and the price is labelled where its line ends.
//  The collision resolver is mirrored line for line by
//  backend/tests/test_ios_report_target_badges.py.
//

import SwiftUI

struct CaydexFairValueRangeChart: View {
    /// Closes, oldest → newest. The caller picks the source and the window.
    let prices: [Double]
    /// The price the dashed line marks: the price frozen with a report, or the live price.
    let currentPrice: Double?
    /// The pole is drawn only for an `.estimate`; a refusal or nil draws the price alone.
    let estimate: CaydexFairValue?
    /// "Price · Sep 2024 – Sep 2026" (see `CaydexFairValue.pricePeriodLabel`).
    var periodLabel: String? = nil
    /// "Price at report time" in a report, "Current price" on the Analysis tab (the legend).
    var priceLegend: String = "Current price"
    var height: CGFloat = 200

    /// Shared with the report's 13F volume bars, which sit under this chart and put their axis
    /// labels in the same column.
    enum Layout {
        static let leadingPadding: CGFloat = 8
        /// The right-hand column: the pole's caps, then the labels.
        static let trailingGutter: CGFloat = 100
        /// The price line ends this far short of the pole.
        static let poleGap: CGFloat = 24
        /// From the end of the plot to the labels' leading edge (clears the pole's caps).
        static let labelGap: CGFloat = 10
        static var labelWidth: CGFloat { trailingGutter - labelGap }
    }

    // MARK: - Inputs, sanitised

    /// Only a positive, finite price is marked. A frozen report whose price fell through
    /// every fallback carries 0.0.
    private var validPrice: Double? {
        guard let p = currentPrice, p.isFinite, p > 0 else { return nil }
        return p
    }

    /// Finite, positive closes. The last point is pinned to the price so the line meets the
    /// dashed line — but only when the series' own last close survived the filter; pinning
    /// after a dropped last close would move an older point instead.
    private var series: [Double] {
        guard let lastRaw = prices.last else { return [] }
        var clean = prices.filter { $0.isFinite && $0 > 0 }
        if let p = validPrice, lastRaw.isFinite, lastRaw > 0, !clean.isEmpty {
            clean[clean.count - 1] = p
        }
        return clean
    }

    private var bounds: (low: Double, value: Double, high: Double)? { estimate?.bounds }
    private var showsPole: Bool { bounds != nil }
    /// A single point would draw as a stray dot at the left edge.
    private var showsLine: Bool { series.count >= 2 }
    /// Whether the right-hand column has anything to label.
    private var showsColumn: Bool { showsPole || validPrice != nil }

    /// Every price the chart must keep on screen: the line, the price, and the whole range —
    /// so a range far from the price (GOOGL +106 %, UNH −62 % on 2026-09-26) is never clipped.
    private var priceUniverse: [Double] {
        var all: [Double] = showsLine ? series : []
        if let b = bounds {
            all.append(contentsOf: [b.low, b.value, b.high])
        }
        if let p = validPrice {
            all.append(p)
        }
        return all
    }

    private var minPrice: Double {
        let all = priceUniverse
        let lo = all.min() ?? 0
        let hi = all.max() ?? 0
        return lo - (hi - lo) * 0.08
    }

    private var maxPrice: Double {
        let all = priceUniverse
        let lo = all.min() ?? 0
        let hi = all.max() ?? 0
        return hi + (hi - lo) * 0.08
    }

    // MARK: - Body

    var body: some View {
        if showsLine || showsPole {
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                if let periodLabel {
                    Text(periodLabel)
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)
                }
                plot
                    .frame(height: height)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(accessibilityText)
                legend
            }
        }
    }

    private var plot: some View {
        GeometryReader { geometry in
            let chartWidth = max(geometry.size.width - Layout.trailingGutter - Layout.leadingPadding, 1)
            // One coordinate system: every mark and every label resolves its y through
            // `yPosition(for:in:)`, so the line, the dashed price line, the pole and the column
            // share one scale.
            ZStack {
                if showsLine {
                    priceLineChart(chartWidth: chartWidth, in: geometry)
                }
                if let price = validPrice {
                    currentPriceIndicator(price, chartWidth: chartWidth, in: geometry)
                }
                if let b = bounds {
                    rangePole(b, chartWidth: chartWidth, in: geometry)
                }
                if showsColumn {
                    labelColumn(chartWidth: chartWidth, in: geometry)
                }
            }
        }
    }

    // MARK: - Line and price

    private func priceLineChart(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        Path { path in
            let points = series
            guard points.count >= 2 else { return }
            // End the line short of the pole so its endpoint meets the dashed price line
            // without crowding the pole; the dashed line continues across to it.
            let lineWidth = max(chartWidth - Layout.poleGap, 1)
            let xStep = lineWidth / CGFloat(points.count - 1)
            path.move(to: CGPoint(x: Layout.leadingPadding, y: yPosition(for: points[0], in: geometry)))
            for (index, price) in points.enumerated() {
                let x = Layout.leadingPadding + CGFloat(index) * xStep
                path.addLine(to: CGPoint(x: x, y: yPosition(for: price, in: geometry)))
            }
        }
        .stroke(AppColors.primaryBlue, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
    }

    /// The dashed price line runs to the end of the plot, where the column's "Price" label
    /// picks it up.
    private func currentPriceIndicator(_ price: Double, chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let yPos = yPosition(for: price, in: geometry)
        return Path { path in
            path.move(to: CGPoint(x: Layout.leadingPadding, y: yPos))
            path.addLine(to: CGPoint(x: Layout.leadingPadding + chartWidth, y: yPos))
        }
        .stroke(AppColors.textSecondary, style: StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
    }

    // MARK: - The range pole

    /// The pole runs exactly from low to high, with flat caps at both ends and a dot at the
    /// estimate. Neutral accent ink: the estimate is a model value, not a good or bad sign.
    @ViewBuilder
    private func rangePole(_ b: (low: Double, value: Double, high: Double), chartWidth: CGFloat,
                           in geometry: GeometryProxy) -> some View {
        let xPos = Layout.leadingPadding + chartWidth - 3
        let highY = yPosition(for: b.high, in: geometry)
        let valueY = yPosition(for: b.value, in: geometry)
        let lowY = yPosition(for: b.low, in: geometry)

        Group {
            RoundedRectangle(cornerRadius: 3)
                .fill(AppColors.accentGraphic.opacity(0.35))
                .frame(width: 6, height: max(lowY - highY, 2))
                .position(x: xPos, y: (highY + lowY) / 2)

            Capsule()
                .fill(AppColors.accentGraphic)
                .frame(width: 14, height: 3)
                .position(x: xPos, y: highY)

            Capsule()
                .fill(AppColors.accentGraphic)
                .frame(width: 14, height: 3)
                .position(x: xPos, y: lowY)

            Circle()
                .fill(AppColors.accentGraphic)
                .frame(width: 12, height: 12)
                .position(x: xPos, y: valueY)
        }
    }

    // MARK: - The label column

    /// One row of the right-hand column: what it names, its price, and the y of its mark.
    private struct ColumnLabel {
        let y: CGFloat
        /// Breaks a tie in y, so equal marks keep a fixed order (High above Estimate above Low).
        let rank: Int
        let name: String
        let price: String
    }

    /// Every row, sorted top → bottom by the y of its mark.
    private func columnLabels(in geometry: GeometryProxy) -> [ColumnLabel] {
        var rows: [ColumnLabel] = []
        if let b = bounds {
            rows.append(ColumnLabel(y: yPosition(for: b.high, in: geometry), rank: 0,
                                    name: "High", price: formatBadgePrice(b.high)))
            rows.append(ColumnLabel(y: yPosition(for: b.value, in: geometry), rank: 1,
                                    name: "Estimate", price: formatBadgePrice(b.value)))
            rows.append(ColumnLabel(y: yPosition(for: b.low, in: geometry), rank: 2,
                                    name: "Low", price: formatBadgePrice(b.low)))
        }
        if let p = validPrice {
            rows.append(ColumnLabel(y: yPosition(for: p, in: geometry), rank: 3,
                                    name: "Price", price: formatBadgePrice(p)))
        }
        return rows.sorted { ($0.y, $0.rank) < ($1.y, $1.rank) }
    }

    @ViewBuilder
    private func labelColumn(chartWidth: CGFloat, in geometry: GeometryProxy) -> some View {
        let rows = columnLabels(in: geometry)
        let anchors = Self.resolvedLabelPositions(
            rows.map(\.y),
            minGap: labelMinGap,
            top: labelInset,
            bottom: geometry.size.height - labelInset
        )
        // Every row starts at the SAME x, so the column reads as one aligned list.
        let centerX = Layout.leadingPadding + chartWidth + Layout.labelGap + Layout.labelWidth / 2

        ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
            columnLabel(row)
                .frame(width: Layout.labelWidth, alignment: .leading)
                .position(x: centerX, y: anchors[index])
        }
    }

    /// "High $266.46" on ONE line: the name muted, the price bold.
    private func columnLabel(_ row: ColumnLabel) -> some View {
        HStack(spacing: 4) {
            Text(row.name)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Text(row.price)
                .font(AppTypography.caption).fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }

    // MARK: Label geometry

    /// One label line, scaled the way `AppTypography.caption` (11pt) scales, so the spacing
    /// below stays true at every Dynamic Type size.
    private var labelLineHeight: CGFloat {
        AppTypography.scaledSize(13, .caption2, maxScale: AppTypography.readingCap)
    }

    /// Two labels cannot share a line: consecutive rows need a full line plus 3pt of air.
    private var labelMinGap: CGFloat {
        labelLineHeight + 3
    }

    /// The nearest a label's centre may come to the top or bottom edge.
    private var labelInset: CGFloat {
        labelLineHeight / 2 + 1
    }

    /// Where the labels go, given the y of their marks (sorted top → bottom).
    ///
    /// Each label keeps its mark's y when it can. Labels that would overlap are grouped, and a
    /// group is spaced `minGap` apart and CENTRED on the mean of its marks — so a crowded group
    /// spreads around its marks instead of sliding one way. Then the column is kept inside
    /// `[top, bottom]`: pushed down from the top, then up from the bottom; a column too short
    /// for every label keeps its order and the top wins. Pure and static so the maths is
    /// testable.
    static func resolvedLabelPositions(
        _ ys: [CGFloat], minGap: CGFloat, top: CGFloat, bottom: CGFloat
    ) -> [CGFloat] {
        guard !ys.isEmpty else { return [] }
        let gap = max(0, minGap)
        let lowest = max(top, bottom)

        var starts: [CGFloat] = []
        var counts: [Int] = []
        var sums: [CGFloat] = []
        for y in ys {
            starts.append(y)
            counts.append(1)
            sums.append(y)
            while starts.count >= 2 {
                let j = starts.count - 1
                if starts[j - 1] + CGFloat(counts[j - 1]) * gap <= starts[j] { break }
                counts[j - 1] += counts[j]
                sums[j - 1] += sums[j]
                starts.removeLast()
                counts.removeLast()
                sums.removeLast()
                let n = CGFloat(counts[j - 1])
                starts[j - 1] = sums[j - 1] / n - (n - 1) * gap / 2
            }
        }

        var out: [CGFloat] = []
        for (start, count) in zip(starts, counts) {
            for k in 0..<count {
                out.append(start + CGFloat(k) * gap)
            }
        }

        out[0] = max(out[0], top)
        for i in 1..<out.count {
            out[i] = max(out[i], out[i - 1] + gap)
        }
        let last = out.count - 1
        if out[last] > lowest {
            out[last] = lowest
            for i in stride(from: last - 1, through: 0, by: -1) {
                out[i] = min(out[i], out[i + 1] - gap)
            }
        }
        return out.map { min(max($0, top), lowest) }
    }

    /// Label price — cents only when the value has them ("$249.53"); whole numbers stay
    /// clean ("$400").
    private func formatBadgePrice(_ value: Double) -> String {
        // Four-digit values drop their cents: "$5,800.50" does not fit the column.
        if value == value.rounded() || abs(value) >= 1000 {
            return CaydexFairValue.wholeMoney(value)
        }
        return String(format: "$%.2f", value)
    }

    /// Every mark (line, dashed price line, pole) and every label maps through this one
    /// function. With a label column it maps into the plot inset by half a label line, so the
    /// top and bottom labels are never clamped away from their marks; without one the full
    /// height is used.
    private func yPosition(for price: Double, in geometry: GeometryProxy) -> CGFloat {
        let priceRange = maxPrice - minPrice
        let top: CGFloat = showsColumn ? labelInset : 0
        let bottom: CGFloat = showsColumn ? labelInset : 0
        let plotHeight = max(geometry.size.height - top - bottom, 1)
        guard priceRange > 0 else { return top + plotHeight / 2 }

        let normalizedValue = (price - minPrice) / priceRange
        return top + plotHeight * (1 - normalizedValue)
    }

    // MARK: - Legend

    /// What the two marks mean. The numbers are on the chart itself, in the label column.
    @ViewBuilder
    private var legend: some View {
        if validPrice != nil || showsPole {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: AppSpacing.md) {
                    legendEntries
                }
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    legendEntries
                }
            }
        }
    }

    @ViewBuilder
    private var legendEntries: some View {
        if validPrice != nil {
            HStack(spacing: AppSpacing.xs) {
                dashSwatch
                Text(priceLegend)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
            }
        }
        if showsPole {
            HStack(spacing: AppSpacing.xs) {
                rangeSwatch
                Text(CaydexFairValue.rangeLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
            }
        }
    }

    private var dashSwatch: some View {
        Path { path in
            path.move(to: CGPoint(x: 0, y: 1))
            path.addLine(to: CGPoint(x: 14, y: 1))
        }
        .stroke(AppColors.textSecondary, style: StrokeStyle(lineWidth: 1.5, dash: [3, 2]))
        .frame(width: 14, height: 2)
        .accessibilityHidden(true)
    }

    /// A small copy of the pole, in the pole's own ink.
    private var rangeSwatch: some View {
        RoundedRectangle(cornerRadius: 2)
            .fill(AppColors.accentGraphic)
            .frame(width: 4, height: 12)
            .accessibilityHidden(true)
    }

    // MARK: - Accessibility

    /// What the header does not already say: the window, and where the price sits.
    private var accessibilityText: String {
        var parts = ["Price chart"]
        if let periodLabel {
            parts.append(periodLabel.replacingOccurrences(of: "Price · ", with: "")
                .replacingOccurrences(of: " – ", with: " to "))
        }
        if let price = validPrice {
            var line = "\(priceLegend) \(CaydexFairValue.money(price))"
            if let where_ = estimate?.pricePosition(of: price) {
                line += ", \(where_)"
            }
            parts.append(line)
        }
        return parts.joined(separator: ". ")
    }
}

// MARK: - Previews

/// A synthetic ramp with a wobble. Typed one step at a time — a one-line closure mixing
/// literals, `Double(_:)` and `sin` is too slow for the type-checker.
private func previewSeries(count: Int, start: Double, step: Double, wobble: Double) -> [Double] {
    var out: [Double] = []
    out.reserveCapacity(count)
    for i in 0..<count {
        let x = Double(i)
        let trend: Double = start + x * step
        let wave: Double = sin(x / 6) * wobble
        out.append(trend + wave)
    }
    return out
}

#Preview("Estimate below the price") {
    CaydexFairValueRangeChart(
        prices: previewSeries(count: 120, start: 180, step: 1.4, wobble: 9),
        currentPrice: 341.07,
        estimate: .sampleEstimate,
        periodLabel: "Price · Sep 2024 – Sep 2026",
        priceLegend: "Price at report time"
    )
    .padding()
}

#Preview("Refused — price only") {
    CaydexFairValueRangeChart(
        prices: previewSeries(count: 120, start: 250, step: 0, wobble: 20),
        currentPrice: 262,
        estimate: .sampleRefused,
        periodLabel: "Price · Sep 2024 – Sep 2026"
    )
    .padding()
}

#Preview("Degenerate inputs") {
    VStack(spacing: 24) {
        // Four-digit values, the range above the price.
        CaydexFairValueRangeChart(
            prices: previewSeries(count: 60, start: 900, step: 3, wobble: 0),
            currentPrice: 1_080,
            estimate: CaydexFairValue(state: .estimate(value: 5_812.4, low: 4_950, high: 6_700)),
            height: 160
        )
        // low == estimate == high, no series, a 0 price.
        CaydexFairValueRangeChart(
            prices: [],
            currentPrice: 0,
            estimate: CaydexFairValue(state: .estimate(value: 100, low: 100, high: 100)),
            height: 120
        )
    }
    .padding()
}
