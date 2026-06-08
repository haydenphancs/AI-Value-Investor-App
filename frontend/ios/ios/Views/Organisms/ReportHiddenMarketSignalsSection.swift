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
        let visible = showAllCongress ? c.trades : Array(c.trades.prefix(3))
        let hiddenCount = c.trades.count - visible.count
        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Congressional Trades")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                Text(c.period)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            HStack(spacing: AppSpacing.sm) {
                statPill(value: "\(c.numBuyers)", label: c.numBuyers == 1 ? "Buyer" : "Buyers", color: AppColors.bullish)
                statPill(value: "\(c.numSellers)", label: c.numSellers == 1 ? "Seller" : "Sellers", color: AppColors.bearish)
                statPill(value: c.netDirection.capitalized, label: "Net", color: netColor)
            }

            // Who actually traded — top 3, expandable ("Show N more"). Reuses the
            // Holders → Congress row (names/amounts match) at the insight size.
            if !c.trades.isEmpty {
                VStack(spacing: AppSpacing.xs) {
                    ForEach(visible) { trade in
                        CongressActivityRow(
                            activity: trade,
                            background: AppColors.cardBackgroundLight,
                            nameFont: AppTypography.label,
                            valueFont: AppTypography.label.weight(.medium)
                        )
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

    // MARK: - Short interest

    private func shortInterestCard(_ s: ShortInterestSignal) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Short Interest")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            // 12-month dual-axis trend (the hero) — green bars = short interest
            // (shares, left axis), white line = short float % (right axis).
            // Falls back to a note when the FINRA settlement series is absent.
            shortChart(s)

            // Snapshot footer — current % of float, days to cover, 3-month change.
            HStack(spacing: AppSpacing.sm) {
                if let pf = s.percentOfFloat {
                    statPill(value: String(format: "%.1f%%", pf), label: "of Float", color: shortColor(pf))
                }
                if let dtc = s.daysToCover {
                    statPill(value: String(format: "%.1f", dtc), label: "Days to Cover", color: AppColors.textPrimary)
                }
                if let ch = s.change3m {
                    statPill(
                        value: String(format: "%@%.0f%%", ch >= 0 ? "+" : "", ch),
                        label: "vs 3mo",
                        color: ch > 0 ? AppColors.bearish : AppColors.bullish
                    )
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
                HStack(spacing: AppSpacing.md) {
                    legendItem(color: AppColors.primaryBlue, label: pctPerM != nil ? "Short float" : "Short interest")
                    if hasDTC {
                        legendItem(color: .white, label: "Days to cover")
                    }
                }
                ShortInterestTrendChart(points: points, pctPerM: pctPerM)
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
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(color)
                .frame(width: 14, height: 3)
            Text(label)
                .font(.system(size: 10))
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
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Helpers

    private func statPill(value: String, label: String, color: Color) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(color)
                .lineLimit(1).minimumScaleFactor(0.7)
            Text(label)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

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
                .foregroundStyle(Color.white)
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
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
            if hasDTC {
                AxisMarks(position: .trailing, values: daysTickPositions) { value in
                    AxisValueLabel {
                        if let m = value.as(Double.self) {
                            Text(String(format: "%.1f", yToDtc(m)))
                                .font(.system(size: 9))
                                .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
            }
        }
        // Inset the first/last bars from the y-axis labels so they don't touch.
        // Extra trailing room lets the most-recent x-label (far right) render
        // instead of being clipped against the plot edge / right %-axis.
        .chartXScale(range: .plotDimension(startPadding: 10, endPadding: 20))
        .chartXAxis {
            AxisMarks(values: xTickIdx) { value in
                AxisGridLine().foregroundStyle(AppColors.textMuted.opacity(0.1))
                AxisValueLabel {
                    if let i = value.as(Int.self), i >= 0, i < points.count {
                        Text(Self.mmYY.string(from: points[i].date))
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
        }
        .frame(height: 260)
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
