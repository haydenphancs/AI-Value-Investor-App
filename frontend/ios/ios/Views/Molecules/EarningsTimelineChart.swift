//
//  EarningsTimelineChart.swift
//  ios
//
//  Molecule: the "continuity" chart for the Earnings Timeline sheet — one
//  yearly axis flowing historical ACTUAL revenue + EPS into the analyst
//  forecast, with an optional SMOOTH DAILY share-price overlay (toggle).
//
//  Rendered CUSTOM (GeometryReader + Path), the same approach as the Wall
//  Street Consensus chart (ReportConsensusBar.analystPriceChart) — SwiftUI
//  Charts couldn't give a clean continuous price line over annual bars. Year
//  columns are evenly spaced and the whole thing scrolls horizontally for a
//  long (~10-year) span; on open it scrolls so the actual|forecast boundary
//  (the "current year") sits mid-screen. Revenue = bars (forecast lighter)
//  with a YoY % chip above the value (green up / red down, like the module
//  chart); EPS = a line scaled into the revenue domain; price = a daily line
//  normalized into its own band. A shared zero baseline keeps NEGATIVE
//  revenue/EPS readable (bars drop below the line, labels move underneath). A
//  dashed rule sits in the gap between the last actual and the first forecast
//  year.
//

import SwiftUI

struct EarningsTimelineChart: View {
    let timeline: [RevenueProjection]      // gapless actuals -> forecast
    let dailyPrices: [EarningsDailyPricePoint]
    let showPrice: Bool

    private let columnWidth: CGFloat = 68
    private let topPad: CGFloat = 30        // headroom for the 2-line revenue labels
    private let labelStripHeight: CGFloat = 24
    private let sidePad: CGFloat = 10
    private let labelGap: CGFloat = 3       // bar edge -> value block
    private let labelHalfHeight: CGFloat = 11  // half of the 2-line (chip + value) block

    // Stable id for the boundary anchor we scroll to on open.
    private let boundaryAnchorID = "earningsForecastBoundary"
    @State private var didCenter = false

    private struct YP {
        let year: Int
        let revenue: Double
        let eps: Double
        let isForecast: Bool
        let revenueLabel: String
        let revenueYoYText: String?
        let revenueYoYColor: Color
    }
    private var points: [YP] {
        timeline.compactMap { p in
            guard let y = Int(p.period) else { return nil }
            return YP(year: y, revenue: p.revenue, eps: p.eps,
                      isForecast: p.isForecast, revenueLabel: p.revenueLabel,
                      revenueYoYText: p.revenueYoYText, revenueYoYColor: p.revenueYoYColor)
        }
    }

    // Magnitudes (abs) so the EPS scaling and value domain handle NEGATIVE
    // revenue / EPS gracefully instead of collapsing to a 1-floor.
    private var maxAbsRevenue: Double { max(points.map { abs($0.revenue) }.max() ?? 1, 1) }
    private var maxAbsEPS: Double { max(points.map { abs($0.eps) }.max() ?? 1, 1) }
    /// Place the largest-magnitude EPS dot at ~70% of the tallest bar.
    private var epsScaleFactor: Double { (maxAbsRevenue * 0.70) / maxAbsEPS }

    /// Shared value domain across revenue AND eps-scaled-into-revenue, always
    /// including zero so a baseline exists. 15% headroom on the populated
    /// side(s) leaves room for the value labels. All-positive data reduces to
    /// the prior behaviour (min == 0, bars sit on the bottom).
    private var valueDomain: (min: Double, max: Double) {
        let vals = points.flatMap { [$0.revenue, $0.eps * epsScaleFactor] }
        let rawMax = max(vals.max() ?? 0, 0)
        let rawMin = min(vals.min() ?? 0, 0)
        let span = max(rawMax - rawMin, 0.0001)
        let axisMax = rawMax + span * 0.15
        let axisMin = rawMin < 0 ? rawMin - span * 0.15 : 0
        return (axisMin, axisMax)
    }

    private var firstForecastIndex: Int? { points.firstIndex(where: { $0.isForecast }) }

    /// Daily prices mapped into COLUMN space: x = yearIndex + fractionThroughYear
    /// (so a price flows left→right across each year's column). Only the years
    /// that exist on the chart and have price data — the line naturally stops
    /// at "now", left of the forecast.
    private var pricesInColumnSpace: [(colX: Double, price: Double)] {
        guard !dailyPrices.isEmpty, !points.isEmpty else { return [] }
        let yearToIndex = Dictionary(
            uniqueKeysWithValues: points.enumerated().map { ($0.element.year, $0.offset) }
        )
        return dailyPrices.compactMap { dp in
            guard dp.date.count >= 10,
                  let y = Int(dp.date.prefix(4)),
                  let m = Int(dp.date.dropFirst(5).prefix(2)),
                  let d = Int(dp.date.dropFirst(8).prefix(2)),
                  let idx = yearToIndex[y] else { return nil }
            let frac = (Double(m - 1) * 30.4 + Double(d)) / 365.0
            return (Double(idx) + frac, dp.price)
        }
    }
    private var priceBounds: (min: Double, max: Double)? {
        let ps = pricesInColumnSpace.map(\.price)
        guard let lo = ps.min(), let hi = ps.max(), hi > lo else { return nil }
        return (lo, hi)
    }

    private var chartWidth: CGFloat {
        sidePad * 2 + CGFloat(points.count) * columnWidth
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                GeometryReader { geo in
                    let plotWidth = geo.size.width - sidePad * 2
                    let colW = points.isEmpty ? plotWidth : plotWidth / CGFloat(points.count)
                    let plotHeight = geo.size.height - topPad - labelStripHeight
                    let plotBottom = topPad + plotHeight
                    let domain = valueDomain

                    let centerX: (Int) -> CGFloat = { i in sidePad + (CGFloat(i) + 0.5) * colW }
                    // One shared mapping for revenue AND eps-scaled values, with a
                    // common zero so negatives drop below the baseline.
                    let yFor: (Double) -> CGFloat = { v in
                        plotBottom - CGFloat((v - domain.min) / (domain.max - domain.min)) * plotHeight
                    }
                    let zeroY = yFor(0)
                    let priceY: (Double) -> CGFloat = { p in
                        guard let b = priceBounds else { return plotBottom }
                        let norm = (p - b.min) / (b.max - b.min)
                        return plotBottom - (0.08 + 0.84 * CGFloat(norm)) * plotHeight
                    }

                    ZStack(alignment: .topLeading) {
                        // Zero baseline — only drawn when something dips negative.
                        if domain.min < 0 {
                            Path { p in
                                p.move(to: CGPoint(x: sidePad, y: zeroY))
                                p.addLine(to: CGPoint(x: geo.size.width - sidePad, y: zeroY))
                            }
                            .stroke(AppColors.textMuted.opacity(0.25), lineWidth: 1)
                        }

                        // Revenue bars + YoY chip + value label + year label
                        ForEach(Array(points.enumerated()), id: \.offset) { i, pt in
                            let vY = yFor(pt.revenue)
                            let barTop = min(vY, zeroY)
                            let barBottom = max(vY, zeroY)
                            let h = max(barBottom - barTop, 1)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(pt.isForecast
                                      ? AppColors.primaryBlue.opacity(0.5)
                                      : AppColors.primaryBlue)
                                .frame(width: colW * 0.5, height: h)
                                .position(x: centerX(i), y: barTop + h / 2)

                            // YoY % above the revenue value (green up / red down),
                            // matching the Future Forecast module chart. The block
                            // is always two lines (a blank placeholder when there's
                            // no anchor) so the value labels stay aligned. Negative
                            // bars carry their labels BELOW the bar.
                            let labelCenterY = pt.revenue < 0
                                ? barBottom + labelGap + labelHalfHeight
                                : barTop - labelGap - labelHalfHeight
                            VStack(spacing: 1) {
                                Text(pt.revenueYoYText ?? " ")
                                    .font(.system(size: 9))
                                    .foregroundColor(pt.revenueYoYColor)
                                Text(pt.revenueLabel)
                                    .font(.system(size: 9))
                                    .foregroundColor(AppColors.textSecondary)
                            }
                            .fixedSize()
                            .position(x: centerX(i), y: labelCenterY)

                            Text(String(pt.year))
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.textSecondary)
                                .position(x: centerX(i), y: plotBottom + labelStripHeight / 2)
                        }

                        // Dashed actual | forecast boundary (in the gap before it)
                        if let fi = firstForecastIndex, fi > 0 {
                            let bx = sidePad + CGFloat(fi) * colW
                            Path { p in
                                p.move(to: CGPoint(x: bx, y: topPad))
                                p.addLine(to: CGPoint(x: bx, y: plotBottom))
                            }
                            .stroke(AppColors.textMuted.opacity(0.35),
                                    style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        }

                        // EPS line + dots (scaled into the revenue domain, shared
                        // zero — so negative EPS dips below the baseline too)
                        Path { p in
                            for (i, pt) in points.enumerated() {
                                let pos = CGPoint(x: centerX(i), y: yFor(pt.eps * epsScaleFactor))
                                if i == 0 { p.move(to: pos) } else { p.addLine(to: pos) }
                            }
                        }
                        .stroke(AppColors.accentYellow,
                                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        ForEach(Array(points.enumerated()), id: \.offset) { i, pt in
                            Circle()
                                .fill(AppColors.accentYellow)
                                .frame(width: 6, height: 6)
                                .position(x: centerX(i), y: yFor(pt.eps * epsScaleFactor))
                        }

                        // Smooth DAILY price line (normalized into its own band)
                        if showPrice, priceBounds != nil {
                            Path { p in
                                for (j, pt) in pricesInColumnSpace.enumerated() {
                                    let pos = CGPoint(x: sidePad + CGFloat(pt.colX) * colW,
                                                      y: priceY(pt.price))
                                    if j == 0 { p.move(to: pos) } else { p.addLine(to: pos) }
                                }
                            }
                            .stroke(AppColors.accentCyan,
                                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        }

                        // Invisible anchor at the actual|forecast boundary (or the
                        // latest year when there's no forecast) so the sheet opens
                        // with the "current year" mid-screen.
                        if !points.isEmpty {
                            let anchorX = firstForecastIndex.map { sidePad + CGFloat($0) * colW }
                                ?? centerX(points.count - 1)
                            Color.clear
                                .frame(width: 1, height: 1)
                                .position(x: anchorX, y: topPad)
                                .id(boundaryAnchorID)
                        }
                    }
                }
                .frame(width: chartWidth, height: 230)
            }
            .onAppear {
                guard !didCenter else { return }
                didCenter = true
                // Center the boundary on open. The synchronous call positions
                // before first paint when layout is ready; the async call is a
                // safety net for when it isn't yet.
                proxy.scrollTo(boundaryAnchorID, anchor: .center)
                DispatchQueue.main.async {
                    proxy.scrollTo(boundaryAnchorID, anchor: .center)
                }
            }
        }
    }
}
