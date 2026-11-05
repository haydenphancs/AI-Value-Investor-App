//
//  ChartCoordinateSystem.swift
//  ios
//
//  Shared coordinate math for chart rendering
//

import SwiftUI

/// Price → y for the main canvas and the crosshair. Computed ONCE per frame in
/// `TickerChartView` from the bars and overlay values in view, then handed to both, so the
/// crosshair dot always sits on the line it describes (it used to recompute a closes-only
/// range and float off the line whenever an overlay, a candle wick or the baseline widened
/// the canvas's).
struct ChartYRange: Equatable {
    let minValue: Double
    let maxValue: Double

    static let unit = ChartYRange(minValue: 0, maxValue: 1)

    private var span: Double { max(maxValue - minValue, Double.ulpOfOne) }

    func y(_ value: Double, height: CGFloat) -> CGFloat {
        let normalized = (value - minValue) / span
        return height - (CGFloat(normalized) * height * 0.9) - height * 0.05
    }

    /// Min/max over the bars in `range` (high/low too when the chart draws OHLC), every
    /// overlay value in that range, and the baseline. One pass per series, no allocation.
    static func compute(
        series: ChartSeries,
        range: Range<Int>,
        useOHLC: Bool,
        overlays: [TechnicalIndicatorType],
        baseline: Double?
    ) -> ChartYRange {
        guard !range.isEmpty, range.upperBound <= series.count else { return .unit }
        var lo = Double.infinity, hi = -Double.infinity
        func take(_ v: Double?) {
            guard let v, v.isFinite else { return }
            if v < lo { lo = v }
            if v > hi { hi = v }
        }
        for i in range {
            take(series.close[i])
            if useOHLC {
                take(series.high[i])
                take(series.low[i])
            }
        }
        for overlay in overlays {
            switch overlay {
            case .ma20, .ma50, .ma200:
                let values = series.movingAverage(overlay)
                if values.count == series.count { for i in range { take(values[i]) } }
            case .bollingerBands:
                for i in range {
                    take(series.bbUpper[i])
                    take(series.bbLower[i])
                }
            case .volume, .rsi14, .macd, .stochastic:
                break
            }
        }
        take(baseline)
        guard lo.isFinite, hi.isFinite else { return .unit }
        // A flat window (every value equal) would leave a 2e-16 span: the line pins to the
        // bottom and the partly visible edge bar, outside this range, maps ~1e15 heights off
        // the canvas. Centre it in a ±0.5% band instead.
        if hi - lo <= max(abs(hi), abs(lo)) * 1e-9 {
            let mid = (hi + lo) / 2
            let pad = mid != 0 ? abs(mid) * 0.005 : 1
            return ChartYRange(minValue: mid - pad, maxValue: mid + pad)
        }
        return ChartYRange(minValue: lo, maxValue: hi)
    }
}

/// x for a GLOBAL bar index and y for a price, in one canvas.
struct ChartCoordinateSystem {
    let width: CGFloat
    let height: CGFloat
    let yRange: ChartYRange
    let frame: ChartFrame

    var minValue: Double { yRange.minValue }
    var maxValue: Double { yRange.maxValue }

    func xPosition(for index: Int) -> CGFloat {
        frame.x(index, width: width)
    }

    func yPosition(for value: Double) -> CGFloat {
        yRange.y(value, height: height)
    }

    /// Bars per drawn column (1 unless zoomed out past one bar per point).
    var bucket: Int { frame.bucketSize(width) }

    /// Width of one bar slot in points.
    var slotWidth: CGFloat { frame.slotWidth(width) }
}

/// Path building shared by every chart renderer: decimated per pixel column when the
/// window holds more bars than the canvas has points. Decimation is DRAWING ONLY — it keeps
/// each column's first/min/max/last (M4), so the envelope a user sees is exact; values,
/// readouts and crosshair snapping always use the full-resolution bars.
enum ChartPath {
    /// A line through `value(i)` over `range`. A nil value BREAKS the line (an indicator
    /// that does not exist at a bar is never bridged across).
    static func line(
        range: Range<Int>,
        coord: ChartCoordinateSystem,
        value: (Int) -> Double?
    ) -> Path {
        var path = Path()
        var started = false
        func add(_ i: Int, _ v: Double) {
            let p = CGPoint(x: coord.xPosition(for: i), y: coord.yPosition(for: v))
            if started { path.addLine(to: p) } else { path.move(to: p); started = true }
        }
        let bucket = coord.bucket
        if bucket <= 1 {
            for i in range {
                if let v = value(i), v.isFinite { add(i, v) } else { started = false }
            }
            return path
        }
        var s = (range.lowerBound / bucket) * bucket
        while s < range.upperBound {
            let lo = max(s, range.lowerBound), hi = min(s + bucket, range.upperBound)
            var firstI = -1, lastI = -1, minI = -1, maxI = -1
            var minV = Double.infinity, maxV = -Double.infinity
            var firstV = 0.0, lastV = 0.0
            var hasGap = false
            var i = lo
            while i < hi {
                if let v = value(i), v.isFinite {
                    if firstI < 0 { firstI = i; firstV = v }
                    lastI = i; lastV = v
                    if v < minV { minV = v; minI = i }
                    if v > maxV { maxV = v; maxI = i }
                } else {
                    hasGap = true
                }
                i += 1
            }
            if firstI < 0 {
                started = false
            } else if hasGap {
                // A gap inside the column: draw it bar by bar, so the line breaks exactly
                // where the value does not exist. M4 would join the points across it.
                for j in lo..<hi {
                    if let v = value(j), v.isFinite { add(j, v) } else { started = false }
                }
            } else {
                // Emit in bar order (first ≤ extremes ≤ last), never repeating a bar.
                add(firstI, firstV)
                var lastAdded = firstI
                let (aI, aV, bI, bV) = minI <= maxI ? (minI, minV, maxI, maxV) : (maxI, maxV, minI, minV)
                if aI != lastAdded { add(aI, aV); lastAdded = aI }
                if bI != lastAdded { add(bI, bV); lastAdded = bI }
                if lastI != lastAdded { add(lastI, lastV) }
            }
            s += bucket
        }
        return path
    }

    /// Iterate `range` in buckets aligned to absolute indices (`i / bucket`), so columns do
    /// not shimmer while the window pans. `body(lo, hi)` gets a half-open bar range.
    static func forEachBucket(_ range: Range<Int>, bucket: Int, _ body: (Int, Int) -> Void) {
        let b = max(1, bucket)
        var s = (range.lowerBound / b) * b
        while s < range.upperBound {
            body(max(s, range.lowerBound), min(s + b, range.upperBound))
            s += b
        }
    }
}

// MARK: - Trading Day Helper

/// Maps intraday data points to their fractional position within a trading session.
enum TradingDayHelper {

    /// The span of one trading day, in ET minutes from midnight.
    ///
    /// Not every asset trades the equity bell. Crypto runs 24/7 and the FMP
    /// commodity codes are continuously-quoted futures — FMP stamps both from
    /// 00:00 ET (verified: BTCUSD and GCUSD bars start at 00:00, ^GSPC at 09:30).
    /// Measuring those against 09:30–16:00 pushed every overnight bar through the
    /// `max(0, …)` clamp and piled them all on the left edge, which is what the
    /// commodity 1D chart did before this type existed.
    struct SessionWindow: Equatable {
        let openMinute: Int
        let closeMinute: Int

        var length: Int { closeMinute - openMinute }

        /// US equities / ETFs / indices: 09:30 – 16:00 ET.
        static let regular = SessionWindow(openMinute: 9 * 60 + 30, closeMinute: 16 * 60)
        /// The same session on an early-close day (day after Thanksgiving, Christmas
        /// Eve…): 09:30 – 13:00 ET, and no after-hours. Chosen by `TickerChartView`
        /// from the bars' own date — never by `window(for:)`, which is class-only. On
        /// a 16:00 axis a half-day's line stopped at 54% with the card at 100% (F19-7).
        static let halfDay = SessionWindow(openMinute: 9 * 60 + 30, closeMinute: 13 * 60)
        /// Extended hours on an early-close day: pre-market from 04:00 as usual, and the
        /// after-hours session that follows the 13:00 bell runs to 17:00 ET.
        static let extendedHalfDay = SessionWindow(openMinute: 4 * 60, closeMinute: 17 * 60)
        /// Crypto: the whole calendar day. (Commodity futures used to share it; since
        /// Phase 4 a commodity screen is an equity-hours ETF or a once-a-day FRED print.)
        static let roundTheClock = SessionWindow(openMinute: 0, closeMinute: 24 * 60)
        /// US equities with pre-market and after-hours: 04:00 – 20:00 ET (960 min).
        ///
        /// Selected only by `window(for:extendedHours:)`, never by `window(for:)`, and
        /// only when the series in hand actually carries such bars. FMP serves them
        /// on request (`extended=true`) — the Extended Hours toggle used to leave the
        /// chart on the 390-minute bell, so the 04:00–09:29 prints piled up on the
        /// left edge under the `max(0, …)` clamp (TestFlight, build 1.0 (8)).
        static let extended = SessionWindow(openMinute: 4 * 60, closeMinute: 20 * 60)
    }

    /// The session an asset class trades in with Extended Hours OFF. Agrees with the
    /// backend card sparkline (`chart_helper.intraday_span`, which always measures
    /// equities on the bell), so a ticker's card and its chart stop at the same place.
    /// With the toggle ON the detail chart deliberately spans 04:00–20:00 while the
    /// card stays on the bell — see `window(for:extendedHours:)`.
    static func window(for context: ChartAssetContext) -> SessionWindow {
        switch context {
        case .crypto:
            return .roundTheClock
        case .stock, .etf, .index, .commodity:
            // `.commodity` moved here in Phase 4: GLD's 1D bars are 09:30–16:00 ET
            // (fetched on the regular session), and drawing them on a 24-hour axis
            // filled two-thirds of the width with the "now" cursor in the wrong place.
            return .regular
        }
    }

    /// The window for a chart whose bars were fetched with the extended-hours flag.
    ///
    /// Only ever WIDENS the regular equity bell to 04:00–20:00. A round-the-clock
    /// asset keeps its own window (crypto is already 00:00–24:00), so this can never
    /// narrow or re-window anything the `window(for:)` switch decided.
    static func window(for context: ChartAssetContext, extendedHours: Bool) -> SessionWindow {
        let base = window(for: context)
        return (extendedHours && base == .regular) ? .extended : base
    }

    /// One bar's share of `window` on the time-mapped 1D chart: the LOWER median minute gap
    /// between consecutive bars over the session length, capped at the interval in hand — so
    /// a halt or a missing bar never widens the bars (with two gaps, the upper median IS the
    /// halt). A single bar has no gap: it is drawn one interval wide (5 minutes if unknown).
    static func slotFraction(minutes: ArraySlice<Int>, window: SessionWindow, intervalMinutes: Int? = nil) -> CGFloat {
        guard window.length > 0 else { return 1 }
        var gaps: [Int] = []
        gaps.reserveCapacity(minutes.count)
        var previous: Int?
        for minute in minutes where minute >= 0 {
            // `minute > p` skips a wrap past midnight on a round-the-clock session.
            if let p = previous, minute > p { gaps.append(minute - p) }
            previous = minute
        }
        var gap: Int
        if gaps.isEmpty {
            gap = intervalMinutes ?? 5
        } else {
            gaps.sort()
            gap = gaps[(gaps.count - 1) / 2]
        }
        if let intervalMinutes, intervalMinutes > 0 { gap = min(gap, intervalMinutes) }
        return min(1, CGFloat(max(gap, 1)) / CGFloat(window.length))
    }

    // Regular session: 9:30 AM - 4:00 PM ET  (570 - 960 minutes from midnight)
    static let marketOpenMinute = SessionWindow.regular.openMinute   // 570
    static let marketCloseMinute = SessionWindow.regular.closeMinute // 960
    static let sessionLength = SessionWindow.regular.length          // 390 minutes

    /// Filter price points to only the latest trading day.
    /// Uses the date prefix (yyyy-MM-dd) of the last data point as the reference day.
    static func filterToLatestDay(_ pricePoints: [StockPricePoint]) -> [StockPricePoint] {
        guard let lastDate = pricePoints.last?.date.prefix(10) else { return pricePoints }
        let latestDay = String(lastDate)
        return pricePoints.filter { $0.date.hasPrefix(latestDay) }
    }

    /// Normalized [0..1] session fractions from ET minutes-of-day (`ChartSeries.minuteOfDay`)
    /// — no DateFormatter per bar. Same clamp as the price-point overload below.
    static func timeFractions(minutes: ArraySlice<Int>, window: SessionWindow = .regular) -> [CGFloat] {
        guard window.length > 0 else { return minutes.map { _ in 0 } }
        return minutes.map { minute in
            guard minute >= 0 else { return 0 }
            let fraction = CGFloat(minute - window.openMinute) / CGFloat(window.length)
            return max(0, min(1, fraction))
        }
    }

    /// Compute normalized [0..1] time fractions for each price point.
    /// 0.0 = the session open, 1.0 = the session close, in `window`.
    /// Points outside the window clamp to the nearest edge.
    ///
    /// `window` defaults to the equity session so existing callers are unchanged.
    /// Pass `window(for:)` for anything that might be crypto or a commodity —
    /// on the 09:30–16:00 window every one of their overnight bars clamps to 0
    /// and stacks on the left edge.
    static func timeFractions(
        for pricePoints: [StockPricePoint],
        window: SessionWindow = .regular
    ) -> [CGFloat] {
        let etTimeZone = TimeZone(identifier: "America/New_York")!
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = etTimeZone

        // A zero/negative-length window would divide by zero and yield NaN x
        // positions, which silently blanks the whole Path.
        guard window.length > 0 else { return pricePoints.map { _ in 0 } }

        return pricePoints.map { point in
            guard let date = ChartDateFormatters.parseDate(point.date) else {
                return 0
            }
            let comps = calendar.dateComponents([.hour, .minute], from: date)
            guard let hour = comps.hour, let minute = comps.minute else { return 0 }
            let minuteOfDay = hour * 60 + minute

            let fraction = CGFloat(minuteOfDay - window.openMinute) / CGFloat(window.length)
            return max(0, min(1, fraction))
        }
    }

    /// Generate evenly-spaced time labels across the trading session in NEW YORK time, the
    /// clock the bars are stamped in. Returns `count` labels like
    /// ["9:30 AM", "11:40 AM", "1:50 PM", "4:00 PM"] on the regular bell, on every phone: in
    /// the phone's zone a Denver axis read 7:30 AM - 2:00 PM under a 9:30 - 4:00 session.
    ///
    /// `window` must be the SAME one `timeFractions` used, or the axis labels
    /// describe a different day than the line above them — a 24/7 asset would be
    /// captioned 9:30 AM–4:00 PM while its bars span midnight to midnight.
    static func sessionTimeLabels(
        count: Int,
        referenceDate: Date? = nil,
        window: SessionWindow = .regular
    ) -> [String] {
        guard count > 1, window.length > 0 else { return [] }

        let etTimeZone = TimeZone(identifier: "America/New_York")!
        var etCalendar = Calendar(identifier: .gregorian)
        etCalendar.timeZone = etTimeZone

        // The New York day of the reference bar (or of now).
        let day = etCalendar.startOfDay(for: referenceDate ?? Date())

        // Market-time formatter (static: this ran a new DateFormatter on every chart pass).
        let formatter = ChartDateFormatters.timeOnly

        var labels: [String] = []
        for i in 0..<count {
            let minuteOffset = Double(i) * Double(window.length) / Double(count - 1)
            // A WALL-CLOCK minute of the New York day, as `timeFractions` places each bar —
            // never elapsed minutes, which slip an hour on a DST Sunday's 23- or 25-hour
            // round-the-clock day. `% 24`: the 24:00 end of that day reads "12:00 AM".
            let minuteOfDay = window.openMinute + Int(minuteOffset)
            guard let labelDate = etCalendar.date(
                bySettingHour: (minuteOfDay / 60) % 24,
                minute: minuteOfDay % 60,
                second: 0,
                of: day
            ) else { continue }
            labels.append(formatter.string(from: labelDate))
        }
        return labels
    }
}
