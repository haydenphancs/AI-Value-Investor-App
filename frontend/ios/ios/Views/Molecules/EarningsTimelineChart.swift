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
//  chart); EPS = a line scaled into the revenue domain on a ROBUST reference
//  (a lone extreme year is capped + pinned to the band edge, not allowed to
//  flatten the rest); price = a daily line normalized into its own band. A
//  shared zero baseline keeps NEGATIVE
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
    /// Column the user tapped → drives the inspect popup. nil = hidden. Owned by
    /// the section (ReportFutureForecastSection) via a binding, so a tap anywhere
    /// outside the chart can clear it — see that section's `.onTapGesture`.
    @Binding var selectedIndex: Int?
    /// Daily price series parsed into column space ONCE (see computePriceColumns).
    /// Held in @State so toggling Price / opening the popup / scrolling never
    /// re-parses the ~1500 date strings on the render path.
    @State private var priceColumns: [(colX: Double, price: Double)] = []

    private struct YP {
        let year: Int
        let revenue: Double
        let eps: Double
        let isForecast: Bool
        let revenueLabel: String
        let revenueYoYText: String?
        let revenueYoYColor: Color
        let epsLabel: String
        let epsYoYText: String?
        let epsYoYColor: Color
        let revenueAnalystCount: Int?
        let epsAnalystCount: Int?
    }
    private var points: [YP] {
        timeline.compactMap { p in
            guard let y = Int(p.period) else { return nil }
            return YP(year: y, revenue: p.revenue, eps: p.eps,
                      isForecast: p.isForecast, revenueLabel: p.revenueLabel,
                      revenueYoYText: p.revenueYoYText, revenueYoYColor: p.revenueYoYColor,
                      epsLabel: p.epsLabel,
                      epsYoYText: p.epsYoYText, epsYoYColor: p.epsYoYColor,
                      revenueAnalystCount: p.revenueAnalystCount,
                      epsAnalystCount: p.epsAnalystCount)
        }
    }

    // Magnitudes (abs) so the EPS scaling and value domain handle NEGATIVE
    // revenue / EPS gracefully instead of collapsing to a 1-floor.
    private var maxAbsRevenue: Double { max(points.map { abs($0.revenue) }.max() ?? 1, 1) }
    private var maxAbsEPS: Double { max(points.map { abs($0.eps) }.max() ?? 1, 1) }

    /// Median of the non-zero |EPS| — the "typical" magnitude, used to spot a
    /// lone outlier without being dragged down by empty/zero years.
    private var medianAbsEPS: Double {
        let vals = points.map { abs($0.eps) }.filter { $0 > 0 }.sorted()
        guard !vals.isEmpty else { return 0 }
        let n = vals.count
        return n % 2 == 1 ? vals[n / 2] : (vals[n / 2 - 1] + vals[n / 2]) / 2
    }
    /// Reference magnitude for EPS scaling that ignores a lone extreme value
    /// (a data glitch or a catastrophic year). When the true max is within 8×
    /// the median it IS the max — uniformly-large stocks (e.g. BRK.A, EPS in
    /// the tens of thousands) and genuine loss years are unaffected. A wild
    /// outlier is capped to 8× median so it can't dominate the scale; it then
    /// pins to the chart edge (see `epsY`) instead of flattening everything.
    private var robustMaxEPS: Double {
        let m = medianAbsEPS
        return max(m > 0 ? min(maxAbsEPS, m * 8) : maxAbsEPS, 1)
    }
    /// Place the largest *normal* EPS dot at ~70% of the tallest bar.
    private var epsScaleFactor: Double { (maxAbsRevenue * 0.70) / robustMaxEPS }

    /// Shared value domain across revenue AND eps-scaled-into-revenue, always
    /// including zero so a baseline exists. 15% headroom on the populated
    /// side(s) leaves room for the value labels. All-positive data reduces to
    /// the prior behaviour (min == 0, bars sit on the bottom).
    private var valueDomain: (min: Double, max: Double) {
        // Revenue always counts. EPS counts only when its scaled value sits
        // within the normal extent (±0.70·maxAbsRevenue, i.e. |eps| ≤
        // robustMaxEPS) — so a clamped outlier year can't stretch the domain and
        // squash the bars; it just pins to the band edge.
        let extent = maxAbsRevenue * 0.70
        let factor = epsScaleFactor
        var vals = points.map(\.revenue)
        for p in points where abs(p.eps * factor) <= extent {
            vals.append(p.eps * factor)
        }
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
    ///
    /// Parsing the ~1500 daily date strings is EXPENSIVE, so this runs ONCE into
    /// `priceColumns` (on load / when the series arrives) — NOT on every render.
    /// The old per-render computed-property version, re-evaluated per point via a
    /// `priceBounds` lookup, was O(n²) over the series and made the chart take
    /// seconds to draw on every toggle/tap.
    private func computePriceColumns() -> [(colX: Double, price: Double)] {
        guard !dailyPrices.isEmpty, !points.isEmpty else { return [] }
        let yearToIndex = Dictionary(
            uniqueKeysWithValues: points.enumerated().map { ($0.element.year, $0.offset) }
        )
        let cols: [(colX: Double, price: Double)] = dailyPrices.compactMap { dp in
            guard dp.date.count >= 10,
                  let y = Int(dp.date.prefix(4)),
                  let m = Int(dp.date.dropFirst(5).prefix(2)),
                  let d = Int(dp.date.dropFirst(8).prefix(2)),
                  let idx = yearToIndex[y] else { return nil }
            let frac = (Double(m - 1) * 30.4 + Double(d)) / 365.0
            return (Double(idx) + frac, dp.price)
        }
        // Start the line at the CENTER of its leftmost data column, not that
        // column's left edge — so the tail sits over the first bar instead of
        // overshooting ~half a column to its left. A price's natural early-year
        // (Jan) start maps to colX ≈ idx (the left edge); we trim that lead-in up
        // to idx + 0.5 (the bar center). Only ever pulls the tail rightward.
        guard let minColX = cols.map(\.colX).min() else { return cols }
        let firstCenter = minColX.rounded(.down) + 0.5
        let clipped = cols.filter { $0.colX >= firstCenter }
        return clipped.isEmpty ? cols : clipped
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
                    // EPS shares the revenue mapping, but its plotted position is
                    // CLAMPED into the band — so a lone extreme/outlier year pins
                    // to the top/bottom edge instead of distorting the whole chart.
                    let epsFactor = epsScaleFactor
                    let epsY: (Double) -> CGFloat = { e in
                        min(max(yFor(e * epsFactor), topPad + 2), plotBottom - 2)
                    }
                    // Price min/max derived ONCE per render from the pre-parsed
                    // columns (a cheap float pass, no strings) and captured by
                    // `priceY` — instead of being re-fetched for every point.
                    let pBounds: (min: Double, max: Double)? = {
                        let ps = priceColumns.map(\.price)
                        guard let lo = ps.min(), let hi = ps.max(), hi > lo else { return nil }
                        return (lo, hi)
                    }()
                    let priceY: (Double) -> CGFloat = { p in
                        guard let b = pBounds else { return plotBottom }
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
                                let pos = CGPoint(x: centerX(i), y: epsY(pt.eps))
                                if i == 0 { p.move(to: pos) } else { p.addLine(to: pos) }
                            }
                        }
                        .stroke(AppColors.accentYellow,
                                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        ForEach(Array(points.enumerated()), id: \.offset) { i, pt in
                            Circle()
                                .fill(AppColors.accentYellow)
                                .frame(width: 6, height: 6)
                                .position(x: centerX(i), y: epsY(pt.eps))
                        }

                        // Smooth DAILY price line (normalized into its own band).
                        // Iterates the pre-parsed `priceColumns` with the captured
                        // `pBounds` — O(n) float math, no per-point re-parse.
                        if showPrice, pBounds != nil {
                            Path { p in
                                for (j, pt) in priceColumns.enumerated() {
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

                        // Transparent tap-catcher on top: a discrete tap maps the
                        // x-location to a column → toggles the inspect popup (tap
                        // the same column again to dismiss). SpatialTapGesture is
                        // discrete, so horizontal scrolling still works.
                        Rectangle()
                            .fill(Color.clear)
                            .contentShape(Rectangle())
                            .gesture(
                                SpatialTapGesture().onEnded { value in
                                    guard !points.isEmpty, colW > 0 else { return }
                                    let raw = Int((value.location.x - sidePad) / colW)
                                    let idx = min(max(raw, 0), points.count - 1)
                                    selectedIndex = (selectedIndex == idx) ? nil : idx
                                }
                            )

                        // Tap-to-inspect: column highlight + detail popup. Drawn
                        // last (on top) but hit-test-disabled, so taps fall through
                        // to the catcher beneath it.
                        if let sel = selectedIndex, points.indices.contains(sel) {
                            let pt = points[sel]
                            let cx = centerX(sel)
                            Path { p in
                                p.move(to: CGPoint(x: cx, y: topPad))
                                p.addLine(to: CGPoint(x: cx, y: plotBottom))
                            }
                            .stroke(AppColors.textSecondary.opacity(0.45),
                                    style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                            .allowsHitTesting(false)

                            inspectPopup(pt)
                                .allowsHitTesting(false)
                                .position(
                                    x: min(max(cx, sidePad + popupHalfWidth),
                                           max(geo.size.width - sidePad - popupHalfWidth,
                                               sidePad + popupHalfWidth)),
                                    y: popupCenterY
                                )
                        }
                    }
                }
                .frame(width: chartWidth, height: 230)
                .animation(.spring(response: 0.3, dampingFraction: 0.8), value: selectedIndex)
            }
            .onAppear {
                // Parse the price series once (it may already be present from a
                // cached load); the .onChange below covers async arrival.
                if priceColumns.isEmpty {
                    priceColumns = computePriceColumns()
                }
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
            .onChange(of: dailyPrices.count) { _, _ in
                // Price arrived (async) or changed — re-parse ONCE here, off the
                // render path, so the body never parses date strings while drawing.
                priceColumns = computePriceColumns()
            }
        }
    }

    // MARK: - Inspect popup

    private let popupHalfWidth: CGFloat = 84   // ~half the (2-column) popup, for edge clamping
    private let popupCenterY: CGFloat = 64     // pushed down so the tighter toggle-chart gap doesn't crowd the button

    /// Detail card for the tapped column: a year header, then TWO columns
    /// (Revenue | EPS), each stacking its dot+label, YoY %, value, and analyst
    /// count. Styled like the Capital Allocation popup (card + shadow + border).
    private func inspectPopup(_ pt: YP) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(String(pt.year))
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textPrimary)
            HStack(alignment: .top, spacing: AppSpacing.lg) {
                popupColumn(color: AppColors.primaryBlue, label: "Revenue",
                            yoy: pt.revenueYoYText, yoyColor: pt.revenueYoYColor,
                            value: pt.revenueLabel, analysts: pt.revenueAnalystCount)
                popupColumn(color: AppColors.accentYellow, label: "EPS",
                            yoy: pt.epsYoYText, yoyColor: pt.epsYoYColor,
                            value: pt.epsLabel, analysts: pt.epsAnalystCount)
            }
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
                .shadow(color: .black.opacity(0.18), radius: 6, x: 0, y: 3)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
        )
        .fixedSize()
    }

    /// One side of the popup — a colored dot + series label, then the YoY %, the
    /// value, and the analyst count stacked beneath. YoY hidden when there's no
    /// anchor; analyst count hidden on actuals (and older reports).
    private func popupColumn(color: Color, label: String,
                             yoy: String?, yoyColor: Color,
                             value: String, analysts: Int?) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
                Circle().fill(color).frame(width: 6, height: 6)
                Text(label)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
            if let yoy {
                Text(yoy)
                    .font(AppTypography.caption)
                    .foregroundColor(yoyColor)
            }
            Text(value)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textPrimary)
            if let analysts {
                Text("\(analysts) Analyst\(analysts == 1 ? "" : "s")")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .frame(minWidth: 58, alignment: .leading)
    }
}
