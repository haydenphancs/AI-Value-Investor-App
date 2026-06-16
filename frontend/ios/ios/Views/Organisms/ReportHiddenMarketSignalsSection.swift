//
//  ReportHiddenMarketSignalsSection.swift
//  ios
//
//  Organism: Hidden Market Signals deep dive — congressional trades (reused
//  from the Holders tab data, so numbers match) + short interest snapshot and
//  a 12-point trend chart + an AI insight.
//

import SwiftUI
import Charts

struct ReportHiddenMarketSignalsSection: View {
    let data: ReportHiddenMarketSignals
    @State private var showAllCongress = false
    // When expanded, a long disclosure list scrolls INSIDE this bounded height
    // instead of stretching the report — same behavior as Insider Activity's
    // "Show N more". A short list (≤ 3) never shows the toggle, so it's unaffected.
    private let expandedListHeight: CGFloat = 420

    private static let dateParser: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if let congress = data.congress {
                congressCard(congress)
            }
            if let si = data.shortInterest {
                shortInterestCard(si)
            }
            if !data.insight.isEmpty {
                insightView(data.insight)
            }
        }
    }

    // MARK: - Congress

    private func congressCard(_ c: CongressSignal) -> some View {
        let netColor: Color = c.netDirection == "buy" ? AppColors.bullish
            : c.netDirection == "sell" ? AppColors.bearish : AppColors.neutral
        // Pills count UNIQUE politicians (num_buyers/num_sellers), matching the
        // Holders → Congress tab — a person who discloses multiple trades counts
        // once. The list below shows the individual disclosures.
        let hiddenCount = c.trades.count - 3
        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Congressional Trades")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                Text(c.period)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            ReportMetricsStrip(metrics: [
                ReportMetricItem(label: c.numBuyers == 1 ? "Buyer" : "Buyers", value: "\(c.numBuyers)", valueColor: AppColors.bullish),
                ReportMetricItem(label: c.numSellers == 1 ? "Seller" : "Sellers", value: "\(c.numSellers)", valueColor: AppColors.bearish),
                ReportMetricItem(label: "Net", value: c.netDirection.capitalized, valueColor: netColor),
            ])

            // Who actually traded — top 3, expandable ("Show N more"). Uses the
            // standard report list row (Key Management style) so it matches the
            // other report lists; 3 lines: name/role/date · range/owner/price.
            // Expanded → the full list scrolls inside a bounded box so a long
            // disclosure list doesn't stretch the report; collapsed → top 3.
            if !c.trades.isEmpty {
                if showAllCongress {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: AppSpacing.md) {
                            ForEach(c.trades) { congressRow($0) }
                        }
                    }
                    .scrollIndicators(.visible)
                    .frame(maxHeight: expandedListHeight)
                } else {
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        ForEach(Array(c.trades.prefix(3))) { congressRow($0) }
                    }
                }
                if c.trades.count > 3 {
                    Button {
                        withAnimation(.easeInOut(duration: 0.2)) { showAllCongress.toggle() }
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Text(showAllCongress ? "Show less" : "Show \(hiddenCount) more")
                                .font(AppTypography.captionEmphasis)
                            Image(systemName: showAllCongress ? "chevron.up" : "chevron.down")
                                .font(AppTypography.iconTiny).fontWeight(.semibold)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.xs)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // One congressional-trade row — shared by the collapsed (top-3) and the
    // expanded scrollable list so they render identically.
    @ViewBuilder
    private func congressRow(_ trade: CongressActivity) -> some View {
        ReportListRow(
            leftPrimary: trade.name,
            leftLines: [
                ReportRowText(text: trade.role),
                ReportRowText(text: trade.formattedDate),
            ],
            rightLines: [
                ReportRowText(text: trade.formattedRange, color: trade.changeColor, isPrimary: true),
                ReportRowText(text: trade.ownerLabel, color: trade.ownerColor),
            ] + (trade.formattedPrice.isEmpty ? [] : [ReportRowText(text: trade.formattedPrice)])
        )
    }

    // MARK: - Short interest

    private func shortInterestCard(_ s: ShortInterestSignal) -> some View {
        let metrics = shortMetrics(s)
        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Short Selling")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            // Snapshot strip (TOP) — % of float · days to cover · 3-month change —
            // in one gray card with "|" dividers (same style as Capital Allocation).
            if !metrics.isEmpty {
                ReportMetricsStrip(metrics: metrics)
            }

            // 12-month dual-axis trend (the hero) — blue bars = short interest
            // (shares, left axis), white line = days to cover (right axis); legend
            // below. Falls back to a note when the FINRA settlement series is absent.
            shortChart(s)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Current snapshot metrics for the strip — % of float, days to cover, 3-mo change.
    private func shortMetrics(_ s: ShortInterestSignal) -> [ReportMetricItem] {
        var m: [ReportMetricItem] = []
        if let pf = s.percentOfFloat {
            m.append(ReportMetricItem(label: "of Float", value: String(format: "%.1f%%", pf), valueColor: shortColor(pf)))
        }
        if let dtc = s.daysToCover {
            m.append(ReportMetricItem(label: "Days to Cover", value: String(format: "%.1f", dtc), valueColor: AppColors.textPrimary))
        }
        if let ch = s.change3m {
            m.append(ReportMetricItem(
                label: "vs 3mo",
                value: String(format: "%@%.0f%%", ch >= 0 ? "+" : "", ch),
                valueColor: ch > 0 ? AppColors.bearish : AppColors.bullish
            ))
        }
        return m
    }

    @ViewBuilder
    private func shortChart(_ s: ShortInterestSignal) -> some View {
        // Filter first, then index the survivors 0..<count so bars sit on an
        // EVENLY-spaced ordinal x. (FINRA settlement dates aren't perfectly
        // evenly spaced, so a raw-date x-axis gives uneven gaps between bars.)
        let valid = s.history.compactMap { p -> (Date, Double, Double?)? in
            guard let ds = p.settlementDate,
                  let d = Self.dateParser.date(from: ds),
                  let ss = p.sharesShort else { return nil }
            return (d, ss / 1_000_000, p.daysToCover)
        }
        let points: [SIPoint] = valid.enumerated().map { i, t in
            SIPoint(idx: i, date: t.0, sharesM: t.1, dtc: t.2)
        }
        // % of float per million shares — lets the LEFT axis re-label the
        // shares domain as a % scale (constant-float approximation).
        let pctPerM: Double? = {
            guard let pf = s.percentOfFloat, let ss = s.sharesShort, ss > 0 else { return nil }
            return pf / (ss / 1_000_000)
        }()
        // Days-to-cover drives the white line + right axis — independent of the
        // bars (it divides by avg volume). Hidden when too few points carry it.
        let hasDTC = points.compactMap { $0.dtc }.count >= 2

        if points.count >= 2 {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ShortInterestTrendChart(points: points, pctPerM: pctPerM)
                // Legend BELOW the chart, with dots (matches the other charts).
                HStack(spacing: AppSpacing.md) {
                    legendItem(color: AppColors.primaryBlue, label: pctPerM != nil ? "Short float" : "Short interest")
                    if hasDTC {
                        legendItem(color: AppColors.textSecondary, label: "Days to cover")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .padding(.top, AppSpacing.xs)
        } else {
            // No FINRA settlement series for this ticker (snapshot-only source)
            // — keep the snapshot pills, but say why the trend is missing.
            Text("12-month trend data unavailable")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)
        }
    }

    private func legendItem(color: Color, label: String) -> some View {
        // Same dot (8×8), spacing, and font (caption) as the Bought/Sold legend
        // (SmartMoneyFlowLegendItem) in Insider Activity.
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    // MARK: - Insight

    private func insightView(_ insight: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles.2")
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(insight)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Helpers

    private func shortColor(_ pctFloat: Double) -> Color {
        if pctFloat >= 10 { return AppColors.bearish }
        if pctFloat >= 5 { return AppColors.alertOrange }
        return AppColors.textPrimary
    }
}

// MARK: - Short-interest trend chart

private struct SIPoint: Identifiable {
    let id = UUID()
    let idx: Int
    let date: Date
    let sharesM: Double
    let dtc: Double?
}

/// Dual-axis combo: blue bars = short interest as % of float (left axis,
/// re-labeled off the shares domain via `pctPerM`); white line = days to
/// cover (right axis, normalized onto the shares domain via its own range).
/// Days-to-cover is independent of the bars (it divides by avg volume), so
/// the line genuinely diverges. Extracted so the type-checker can cope.
private struct ShortInterestTrendChart: View {
    let points: [SIPoint]
    let pctPerM: Double?

    private static let mmYY: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MM/yy"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    var body: some View {
        let vals = points.map { $0.sharesM }
        let lo = vals.min() ?? 0
        let hi = vals.max() ?? 1
        // Zoom the Y window to the data band (short interest barely moves off a
        // high base, so a 0-based axis would be a solid block). `span` guards a
        // flat series; footroom keeps the lowest bars from being slivers.
        let span = max(hi - lo, hi * 0.08)
        let yMin = max(0, lo - span * 0.5)
        // Generous top headroom so the highest round tick (e.g. 40M / ~2.4%)
        // lands clearly ABOVE the peak instead of at the domain edge (where
        // Swift Charts drops it, leaving the top label below the peak).
        let yMax = hi + span * 0.55
        // ~24 biweekly points → thin bars; ~12 monthly → wider.
        let barWidth: MarkDimension = points.count > 14 ? .fixed(6) : .fixed(11)
        // 4 evenly-spaced x-ticks BY INDEX: far-left, ~1/3, ~2/3, far-right.
        let n = points.count
        let xTickIdx: [Int] = n >= 2
            ? Array(Set((0..<4).map { $0 * (n - 1) / 3 })).sorted()
            : Array(0..<n)

        // Days-to-cover line — its OWN scale, normalized into the shares-M
        // domain so it overlays the bars; the RIGHT axis re-labels it in days.
        let dtcPoints = points.filter { $0.dtc != nil }
        let hasDTC = dtcPoints.count >= 2
        let dtcVals = dtcPoints.compactMap { $0.dtc }
        let dLo = dtcVals.min() ?? 0
        let dHi = dtcVals.max() ?? 1
        let dSpan = max(dHi - dLo, max(dHi * 0.08, 0.0001))
        let dMin = max(0, dLo - dSpan * 0.5)
        let dMax = dHi + dSpan * 0.5
        let dRange = dMax - dMin
        let dStep: Double = dRange > 4 ? 1.0 : (dRange > 1.6 ? 0.5 : 0.25)
        let daysTicks: [Double] = {
            guard dStep > 0, dMax > dMin else { return [] }
            var out: [Double] = []
            var t = (dMin / dStep).rounded(.up) * dStep
            while t <= dMax + 0.0001 && out.count < 8 { out.append(t); t += dStep }
            return out
        }()
        func dtcToY(_ d: Double) -> Double { yMin + (d - dMin) / (dMax - dMin) * (yMax - yMin) }
        func yToDtc(_ m: Double) -> Double { dMin + (m - yMin) / (yMax - yMin) * (dMax - dMin) }
        func leftLabel(_ m: Double) -> String {
            if let k = pctPerM { return String(format: "%.1f%%", m * k) }
            return "\(Int(m))M"
        }
        let daysTickPositions = daysTicks.map { dtcToY($0) }

        return Chart {
            ForEach(points) { item in
                BarMark(
                    x: .value("Period", item.idx),
                    y: .value("Short float", item.sharesM),
                    width: barWidth
                )
                .foregroundStyle(AppColors.primaryBlue)
                .cornerRadius(3)
            }
            ForEach(hasDTC ? dtcPoints : []) { item in
                LineMark(
                    x: .value("Period", item.idx),
                    y: .value("Days to cover", dtcToY(item.dtc ?? 0))
                )
                // Was Color.white (== textPrimary, identical to the "2.1%"
                // metric) and read as too bright — use the "Short Selling"
                // header color (textSecondary) instead.
                .foregroundStyle(AppColors.textSecondary)
                .lineStyle(StrokeStyle(lineWidth: 2))
                .interpolationMethod(.catmullRom)
            }
        }
        .chartYScale(domain: yMin...yMax)
        // BarMark anchors at 0, which is below the zoomed floor — clip the plot
        // so bars fill from the baseline up instead of overflowing onto the
        // pills/insight below the chart.
        .chartPlotStyle { plotArea in
            plotArea.clipped()
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(AppColors.textMuted.opacity(0.12))
                AxisValueLabel {
                    if let m = value.as(Double.self) {
                        Text(leftLabel(m))
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
            if hasDTC {
                AxisMarks(position: .trailing, values: daysTickPositions) { value in
                    AxisValueLabel {
                        if let m = value.as(Double.self) {
                            Text(String(format: "%.1f", yToDtc(m)))
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
            }
        }
        // Bars fill the full width (original look) — small symmetric padding only.
        .chartXScale(range: .plotDimension(startPadding: 8, endPadding: 8))
        .chartXAxis {
            AxisMarks(values: xTickIdx) { value in
                AxisGridLine().foregroundStyle(AppColors.textMuted.opacity(0.1))
                // First label anchors leading, last anchors trailing, so the edge
                // dates (incl. the most-recent month) sit fully under their column
                // instead of overflowing / truncating at the plot edge.
                AxisValueLabel(anchor: xLabelAnchor(value.as(Int.self) ?? -1)) {
                    if let i = value.as(Int.self), i >= 0, i < points.count {
                        Text(Self.mmYY.string(from: points[i].date))
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
        }
        .frame(height: 260)
    }

    // Edge x-labels anchor inward (first → leading, last → trailing) so they
    // align under their column without overflowing the plot edge.
    private func xLabelAnchor(_ idx: Int) -> UnitPoint {
        if idx <= 0 { return .topLeading }
        if idx >= points.count - 1 { return .topTrailing }
        return .top
    }
}

#Preview {
    let history: [ShortInterestPoint] = (0..<12).map { (i: Int) -> ShortInterestPoint in
        let shares: Double = Double(28_000_000 + i * 900_000)
        // Wave so the preview shows days-to-cover diverging from the rising bars.
        let dtc: Double = 2.0 + sin(Double(i) * 0.7) * 0.5
        let date: String = String(format: "2025-%02d-15", i + 1)
        return ShortInterestPoint(settlementDate: date, sharesShort: shares, daysToCover: dtc)
    }
    let signal = ShortInterestSignal(
        percentOfFloat: 2.1, daysToCover: 1.6, sharesShort: 38_000_000,
        change3m: 8.0, settlementDate: "2025-12-15", history: history
    )
    let congress = CongressSignal(
        numBuyers: 4, numSellers: 1,
        totalBuysInMillions: 2.3, totalSellsInMillions: 0.4,
        netDirection: "buy", period: "Last 12 Months",
        trades: CongressActivity.sampleData
    )
    let model = ReportHiddenMarketSignals(
        congress: congress, shortInterest: signal,
        insight: "Congress is net buying while short interest climbs to 6.2% of float — a notable tension."
    )
    return ReportHiddenMarketSignalsSection(data: model)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}

#Preview("Snapshot only — no series") {
    let signal = ShortInterestSignal(
        percentOfFloat: 4.2, daysToCover: 2.1, sharesShort: 12_000_000,
        change3m: -3.0, settlementDate: "2025-12-15", history: []
    )
    let model = ReportHiddenMarketSignals(
        congress: nil, shortInterest: signal,
        insight: "Short interest snapshot only — no settlement series available for this ticker."
    )
    return ReportHiddenMarketSignalsSection(data: model)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
