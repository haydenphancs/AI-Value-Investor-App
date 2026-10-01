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
        /// The column's fiscal period END ("yyyy-MM-dd"); nil on older reports.
        let periodEnd: String?
        /// False when the backend sent "N/A" for this year's EPS (genuinely
        /// absent, not a real 0). The EPS marker/segment is skipped for these so
        /// the line doesn't dip to a false zero; the bar + "N/A" label still show.
        var hasEPS: Bool { epsLabel != "N/A" }
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
                      epsAnalystCount: p.epsAnalystCount,
                      periodEnd: p.periodEnd)
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

    /// Daily prices mapped into COLUMN space: x = columnIndex + fractionThroughPeriod
    /// (so a price flows left→right across each year's column). Only the years
    /// that exist on the chart and have price data — the line naturally stops
    /// at "now".
    ///
    /// Columns are FISCAL years. When every column carries its period END (reports
    /// generated after `period_end` shipped), a close belongs to the column whose window
    /// (previous period end, this period end] contains it — NVDA's FY2026 column runs
    /// Feb 2025 – Jan 2026. Mapping by CALENDAR year put calendar-2025 closes over the
    /// FY2025 bar (about 11 months late for NVDA, 7 for ORCL) and today's price over the
    /// last reported year instead of the in-progress forecast column. Older reports
    /// without period ends keep the calendar mapping.
    ///
    /// Parsing the ~1500 daily date strings is EXPENSIVE, so this runs ONCE into
    /// `priceColumns` (on load / when the series arrives) — NOT on every render.
    /// The old per-render computed-property version, re-evaluated per point via a
    /// `priceBounds` lookup, was O(n²) over the series and made the chart take
    /// seconds to draw on every toggle/tap.
    private func computePriceColumns() -> [(colX: Double, price: Double)] {
        guard !dailyPrices.isEmpty, !points.isEmpty else { return [] }
        let cols: [(colX: Double, price: Double)]
        if let edges = periodEndEdges() {
            cols = priceColumnsByPeriodEnd(edges: edges)
        } else {
            cols = priceColumnsByCalendarYear()
        }
        // Start the line at the CENTER of its leftmost data column, not that
        // column's left edge — so the tail sits over the first bar instead of
        // overshooting ~half a column to its left. A price's natural early-period
        // start maps to colX ≈ idx (the left edge); we trim that lead-in up
        // to idx + 0.5 (the bar center). Only ever pulls the tail rightward.
        guard let minColX = cols.map(\.colX).min() else { return cols }
        let firstCenter = minColX.rounded(.down) + 0.5
        let clipped = cols.filter { $0.colX >= firstCenter }
        return clipped.isEmpty ? cols : clipped
    }

    /// The first column has no previous period end on the wire: its window opens this many
    /// days before its own end. The backend's `_TIMELINE_FIRST_COLUMN_DAYS` (which trims the
    /// frozen price series to the same window) uses the same span.
    private static let firstColumnDays = 365

    /// Day numbers of each column's period end, or nil when any column lacks a parseable
    /// one (an older report) or they are not strictly increasing — then the calendar
    /// mapping is used instead.
    private func periodEndEdges() -> [Int]? {
        var edges: [Int] = []
        edges.reserveCapacity(points.count)
        for p in points {
            guard let s = p.periodEnd, let day = Self.dayNumber(s) else { return nil }
            if let last = edges.last, day <= last { return nil }
            edges.append(day)
        }
        return edges.isEmpty ? nil : edges
    }

    /// Column i spans (edges[i-1], edges[i]]; the first spans `firstColumnDays` before its
    /// end. A close outside every window (before the first, after the last) is dropped.
    private func priceColumnsByPeriodEnd(edges: [Int]) -> [(colX: Double, price: Double)] {
        guard let lastEdge = edges.last else { return [] }
        let firstStart: Int = edges[0] - Self.firstColumnDays
        var out: [(colX: Double, price: Double)] = []
        out.reserveCapacity(dailyPrices.count)
        for dp in dailyPrices {
            guard let day = Self.dayNumber(dp.date), day > firstStart, day <= lastEdge else {
                continue
            }
            // First column whose period end is on or after this day (binary search).
            var lo = 0
            var hi = edges.count - 1
            while lo < hi {
                let mid = (lo + hi) / 2
                if edges[mid] < day {
                    lo = mid + 1
                } else {
                    hi = mid
                }
            }
            let start: Int = lo == 0 ? firstStart : edges[lo - 1]
            let span: Int = max(edges[lo] - start, 1)
            let frac: Double = Double(day - start) / Double(span)
            out.append((colX: Double(lo) + frac, price: dp.price))
        }
        return out
    }

    /// Legacy mapping for reports without period ends: the close's CALENDAR year picks the
    /// column, its month/day the fraction through it.
    private func priceColumnsByCalendarYear() -> [(colX: Double, price: Double)] {
        // `uniqueKeysWithValues:` TRAPS on a duplicate key, and older reports did not
        // dedupe the year (`int(date[:4])` of every annual record — a company that moved
        // its fiscal year-end has two period ends inside one calendar year). Keep the LAST
        // occurrence — `points` is ordered oldest-first, so that is the most recent period.
        let yearToIndex = Dictionary(
            points.enumerated().map { ($0.element.year, $0.offset) },
            uniquingKeysWith: { _, latest in latest }
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

    /// Days since 1970-01-01 for a "yyyy-MM-dd" prefix, by integer civil-date arithmetic
    /// (days_from_civil) — no DateFormatter on ~1,500 closes. nil when malformed.
    private static func dayNumber(_ s: String) -> Int? {
        let bytes: [UInt8] = Array(s.utf8.prefix(10))
        guard bytes.count == 10, bytes[4] == 45, bytes[7] == 45 else { return nil }
        var fields: [Int] = [0, 0, 0]
        let ranges: [Range<Int>] = [0..<4, 5..<7, 8..<10]
        for (k, r) in ranges.enumerated() {
            var v = 0
            for i in r {
                let c = bytes[i]
                guard c >= 48, c <= 57 else { return nil }
                v = v * 10 + Int(c - 48)
            }
            fields[k] = v
        }
        let month: Int = fields[1]
        let dayOfMonth: Int = fields[2]
        guard (1...12).contains(month), (1...31).contains(dayOfMonth) else { return nil }
        let y: Int = month <= 2 ? fields[0] - 1 : fields[0]
        let era: Int = (y >= 0 ? y : y - 399) / 400
        let yoe: Int = y - era * 400
        let mp: Int = (month + 9) % 12
        let doy: Int = (153 * mp + 2) / 5 + dayOfMonth - 1
        let doe: Int = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146_097 + doe - 719_468
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
                                    .font(.system(size: 11))
                                    .foregroundColor(pt.revenueYoYColor)
                                Text(pt.revenueLabel)
                                    .font(.system(size: 11))
                                    .foregroundColor(AppColors.textSecondary)
                            }
                            .fixedSize()
                            .position(x: centerX(i), y: labelCenterY)

                            Text(String(pt.year))
                                .font(.system(size: 11))
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
                        // zero — so negative EPS dips below the baseline too). Years
                        // with no EPS ("N/A") are skipped so the line breaks over the
                        // gap instead of dropping to a false zero.
                        Path { p in
                            var penDown = false
                            for (i, pt) in points.enumerated() {
                                guard pt.hasEPS else { penDown = false; continue }
                                let pos = CGPoint(x: centerX(i), y: epsY(pt.eps))
                                if penDown { p.addLine(to: pos) } else { p.move(to: pos); penDown = true }
                            }
                        }
                        .stroke(AppColors.accentYellow,
                                style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        ForEach(Array(points.enumerated()), id: \.offset) { i, pt in
                            if pt.hasEPS {
                                Circle()
                                    .fill(AppColors.accentYellow)
                                    .frame(width: 6, height: 6)
                                    .position(x: centerX(i), y: epsY(pt.eps))
                            }
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
                .cardFill()
                .shadow(color: AppColors.shadowAmbient, radius: 6, x: 0, y: 3)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .strokeBorder(AppColors.cardBackgroundLight, lineWidth: 1)
        )
        .fixedSize()
    }

    /// One side of the popup — a colored dot + series label, then the YoY %, the
    /// value, and the analyst count stacked beneath. YoY hidden when there's no
    /// anchor; analyst count shows "n/a" on actuals / years FMP didn't cover.
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
            } else {
                // Actuals (reported, no analyst coverage) and any forecast year
                // FMP didn't cover show "n/a" rather than a blank — so every year
                // surfaces its analyst count.
                Text("n/a")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .frame(minWidth: 58, alignment: .leading)
    }
}

#Preview("Fiscal-year columns + price overlay") {
    // Sample ORCL-shaped data (fiscal year ends May 31): every column carries its period
    // end, so the price line is placed by fiscal window, not calendar year.
    EarningsTimelineChart(
        timeline: TickerReportData.sampleOracle.revenueForecast.annualTimeline,
        dailyPrices: TickerReportData.sampleOracle.revenueForecast.timelinePrices,
        showPrice: true,
        selectedIndex: .constant(nil)
    )
    .padding()
    .background(AppColors.cardBackground)
}
