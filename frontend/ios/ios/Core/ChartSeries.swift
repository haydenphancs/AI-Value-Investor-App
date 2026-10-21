//
//  ChartSeries.swift
//  ios
//
//  The asset-detail chart's data, built ONCE per data change: the merged bars (older
//  history pages + the range payload) as columns, every indicator over the WHOLE merged
//  series, and the index lookups the viewport needs. Renderers only read it — nothing in
//  here runs per frame.
//
//  Why the indicators are computed over the merged series, never per visible slice: an
//  MA(200), RSI or MACD value depends on the bars BEFORE it. Computing over the slice (or
//  over "the bars before the viewport", as the canvas used to) is what made the lines start
//  partway in and, on 1D, compute across a multi-day hole.
//

import CoreGraphics
import Foundation

nonisolated enum ChartWarmup {
    /// Bars that must exist to the LEFT of the leftmost visible bar while older history can
    /// still be loaded. Mirrors backend `chart_helper._WARMUP_DATA_POINTS`.
    ///
    /// 199 is enough for MA(200) to be exact. 210 also makes the seeded indicators match a
    /// full-history computation: an EMA/Wilder seed error decays by (1 - α) per bar, so after
    /// 210 bars MACD's slow EMA is within (25/27)^210 ≈ 1e-7 and RSI within (13/14)^210 ≈ 2e-7
    /// of the value full history gives — far below what the chart or a readout can show.
    static let bars = 210
}

/// What a `ChartSeries` is built from. Plain values, so the build can run off the main actor.
nonisolated struct ChartSeriesInput: Sendable {
    /// Older history, strictly before `payload.first` (the loader guarantees it; the build
    /// re-checks). Empty on 1D and before the first page.
    let older: [StockPricePoint]
    /// The bars the range response returned — what the screen's model holds.
    let payload: [StockPricePoint]
    /// The default window's left edge in the bars' OWN string format ("yyyy-MM-dd", or
    /// "yyyy-MM-dd HH:mm:ss" ET wall clock for intraday). nil → the payload's first bar.
    let rangeStartCutoff: String?
    /// Caller-supplied identity; changes whenever any input changes.
    let revision: Int
}

nonisolated struct ChartSeries: Sendable {
    let revision: Int

    // MARK: Bars (oldest first, contiguous)
    let dates: [String]
    let open: [Double?]
    let high: [Double?]
    let low: [Double?]
    let close: [Double]
    let volume: [Double?]
    /// Pre/after-hours bar (intraday only) — same rule as `StockPricePoint.isExtendedHours`.
    let isExtended: [Bool]
    /// ET minute of day for intraday bars, -1 for daily and coarser.
    let minuteOfDay: [Int]

    // MARK: Index lookups
    /// Number of bars that came from older history pages = index of the payload's first bar.
    let payloadStart: Int
    /// First bar at or after the range's visible start — the default window's left edge.
    let rangeStartIndex: Int
    /// First bar of the latest session (shares the last bar's yyyy-MM-dd) — the 1D window.
    let latestSessionStart: Int

    // MARK: Indicators over the WHOLE series (nil = does not exist at that bar)
    let sma20: [Double?]
    let sma50: [Double?]
    let sma200: [Double?]
    let bbUpper: [Double?]
    let bbMiddle: [Double?]
    let bbLower: [Double?]
    let rsi14: [Double?]
    let macdLine: [Double?]
    let macdSignal: [Double?]
    let macdHistogram: [Double?]
    /// Slow stochastic (14,3,3): %K = SMA3(raw %K), %D = SMA3(%K).
    let stochK: [Double?]
    let stochD: [Double?]

    var count: Int { close.count }
    var isEmpty: Bool { close.isEmpty }

    static let empty = ChartSeries.build(ChartSeriesInput(older: [], payload: [], rangeStartCutoff: nil, revision: 0))

    /// Index of the first bar whose date is `>= cutoff` (lexicographic = chronological for
    /// fixed-width ISO strings), or `count` when none is.
    func lowerBound(_ cutoff: String) -> Int {
        Self.lowerBound(dates, cutoff)
    }

    /// Index of the first bar dated strictly after `date`, or `count`.
    func upperBound(_ date: String) -> Int {
        var lo = 0, hi = dates.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if dates[mid] <= date { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    // MARK: - Build

    static func lowerBound(_ dates: [String], _ cutoff: String) -> Int {
        var lo = 0, hi = dates.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if dates[mid] < cutoff { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    /// Merge, column-ise and compute every indicator. O(n) except the windowed BB / Stoch
    /// passes (O(n·20) / O(n·14), allocation-free). Safe off the main actor.
    static func build(_ input: ChartSeriesInput) -> ChartSeries {
        let payload = input.payload
        // Defensive contiguity: only older bars strictly before the payload's first bar.
        var older = input.older
        if let first = payload.first?.date, let last = older.last?.date, last >= first {
            let cut = lowerBound(older.map(\.date), first)
            older = Array(older[0..<cut])
        }
        let total = older.count + payload.count

        var dates = [String](); dates.reserveCapacity(total)
        var open = [Double?](); open.reserveCapacity(total)
        var high = [Double?](); high.reserveCapacity(total)
        var low = [Double?](); low.reserveCapacity(total)
        var close = [Double](); close.reserveCapacity(total)
        var volume = [Double?](); volume.reserveCapacity(total)
        var isExtended = [Bool](); isExtended.reserveCapacity(total)
        var minuteOfDay = [Int](); minuteOfDay.reserveCapacity(total)

        func append(_ p: StockPricePoint) {
            dates.append(p.date)
            // A non-positive or non-finite open/high/low is a missing field, never a price.
            open.append(validPrice(p.open))
            high.append(validPrice(p.high))
            low.append(validPrice(p.low))
            close.append(p.close)
            volume.append(p.volume.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil })
            let minute = minuteOfDayET(p.date)
            minuteOfDay.append(minute)
            isExtended.append(minute >= 0 && (minute < 570 || minute >= 960))
        }
        for p in older { append(p) }
        for p in payload { append(p) }

        let payloadStart = older.count
        var rangeStart = payloadStart
        if let cutoff = input.rangeStartCutoff {
            let idx = lowerBound(dates, cutoff)
            // No bar at or after the cutoff (a halted ticker, a pre-open morning after a
            // holiday) → fall back to the payload start, never an empty window.
            rangeStart = idx < total ? idx : payloadStart
        }
        var latestSessionStart = 0
        if let last = dates.last {
            latestSessionStart = lowerBound(dates, String(last.prefix(10)))
        }

        // Indicator inputs. Missing high/low stays MISSING (NaN): the stochastic is nil over
        // any window that holds one, instead of an oscillator fabricated from closes.
        let highs = high.map { $0 ?? .nan }
        let lows = low.map { $0 ?? .nan }

        let sma20 = TechnicalIndicatorCalculator.sma(closes: close, period: 20)
        let sma50 = TechnicalIndicatorCalculator.sma(closes: close, period: 50)
        let sma200 = TechnicalIndicatorCalculator.sma(closes: close, period: 200)
        let bb = TechnicalIndicatorCalculator.bollingerBands(closes: close)
        let rsi = TechnicalIndicatorCalculator.rsi(closes: close, period: 14)
        let macd = TechnicalIndicatorCalculator.macd(closes: close)
        let stoch = TechnicalIndicatorCalculator.slowStochastic(highs: highs, lows: lows, closes: close)

        return ChartSeries(
            revision: input.revision,
            dates: dates, open: open, high: high, low: low, close: close, volume: volume,
            isExtended: isExtended, minuteOfDay: minuteOfDay,
            payloadStart: payloadStart, rangeStartIndex: rangeStart, latestSessionStart: latestSessionStart,
            sma20: sma20, sma50: sma50, sma200: sma200,
            bbUpper: bb.upper, bbMiddle: bb.middle, bbLower: bb.lower,
            rsi14: rsi.values,
            macdLine: macd.macdLine, macdSignal: macd.signalLine, macdHistogram: macd.histogram,
            stochK: stoch.kValues, stochD: stoch.dValues
        )
    }

    private static func validPrice(_ v: Double?) -> Double? {
        guard let v, v.isFinite, v > 0 else { return nil }
        return v
    }

    /// "yyyy-MM-dd HH:mm[:ss]" → hour·60 + minute, read straight from the fixed-width
    /// string (the backend stamps intraday bars in ET wall clock). -1 for a date-only string.
    static func minuteOfDayET(_ date: String) -> Int {
        let u = Array(date.utf8.prefix(16))
        guard u.count >= 16 else { return -1 }
        func digit(_ i: Int) -> Int? {
            let c = u[i]
            return (c >= 48 && c <= 57) ? Int(c - 48) : nil
        }
        guard let h1 = digit(11), let h2 = digit(12), let m1 = digit(14), let m2 = digit(15) else { return -1 }
        return (h1 * 10 + h2) * 60 + m1 * 10 + m2
    }
}

/// The range of GLOBAL bar indices a frame draws, plus the fractional slot mapping that
/// places them. Every renderer, the crosshair and the sub-panes share one of these, so a
/// bar sits at the same x in every pane.
nonisolated struct ChartFrame: Equatable, Sendable {
    /// Bars to draw (partially visible bars at the edges included), global indices.
    let drawRange: Range<Int>
    /// Slot coordinate of the window's left edge. Bar `i`'s centre sits at slot `i + 0.5`.
    let slotLeft: Double
    /// Visible width in bar slots (fractional while pinching/panning).
    let slotCount: Double
    /// 1D only: per-bar x fractions of the session, indexed `i - drawRange.lowerBound`.
    let timeFractions: [CGFloat]?
    /// 1D only: one bar's share of the session width (`TradingDayHelper.slotFraction`). The
    /// bars fill only the elapsed part of the session, so `width / drawRange.count` made a
    /// 3-bar morning's candles, volume bars and shading a third of the chart wide each.
    var timeSlotFraction: CGFloat? = nil

    static let empty = ChartFrame(drawRange: 0..<0, slotLeft: 0, slotCount: 1, timeFractions: nil)

    var isEmpty: Bool { drawRange.isEmpty }

    /// Bars whose CENTRE is on screen: what the price scale, a pane's scale and the baseline
    /// are measured over. `drawRange` also holds the partly visible bar past each edge (so a
    /// line runs to the edge), and a value nobody can see must not stretch the scale.
    var visibleRange: Range<Int> {
        guard timeFractions == nil, slotCount > 0, !drawRange.isEmpty else { return drawRange }
        let lo = max(drawRange.lowerBound, Int((slotLeft - 0.5).rounded(.up)))
        let hi = min(drawRange.upperBound, Int((slotLeft + slotCount - 0.5).rounded(.down)) + 1)
        return lo < hi ? lo..<hi : drawRange
    }

    /// x of bar `i` in a canvas `width` wide.
    func x(_ i: Int, width: CGFloat) -> CGFloat {
        if let f = timeFractions {
            let j = i - drawRange.lowerBound
            guard j >= 0, j < f.count else { return 0 }
            return f[j] * width
        }
        guard slotCount > 0 else { return width / 2 }
        return CGFloat((Double(i) + 0.5 - slotLeft) / slotCount) * width
    }

    /// Width of one bar slot in points.
    func slotWidth(_ width: CGFloat) -> CGFloat {
        if timeFractions != nil {
            if let f = timeSlotFraction, f > 0 { return width * f }
            return width / CGFloat(max(1, drawRange.count))
        }
        return width / CGFloat(max(slotCount, 1))
    }

    /// Bars per pixel column when zoomed out past one bar per point; 1 otherwise. Buckets
    /// are aligned to ABSOLUTE indices (`i / bucket`), so they do not shimmer while panning.
    /// Sized from `slotCount` (fixed during a pan), not `drawRange.count`, which alternates by
    /// one as the edges cross half a slot and flipped the bucket size frame to frame.
    func bucketSize(_ width: CGFloat) -> Int {
        guard timeFractions == nil, width > 0 else { return 1 }
        let perPoint = slotCount / Double(width)
        return perPoint > 1.5 ? Int(perPoint.rounded(.up)) : 1
    }

    /// The bar nearest `x` (crosshair snapping) — always a REAL bar, never a bucket.
    func index(atX x: CGFloat, width: CGFloat) -> Int? {
        guard !drawRange.isEmpty, width > 0 else { return nil }
        if let f = timeFractions {
            let frac = x / width
            var best = drawRange.lowerBound
            var bestDist = CGFloat.greatestFiniteMagnitude
            for j in 0..<min(f.count, drawRange.count) {
                let d = abs(f[j] - frac)
                if d < bestDist { bestDist = d; best = drawRange.lowerBound + j }
            }
            return best
        }
        let slot = slotLeft + Double(x / width) * slotCount - 0.5
        let i = Int(slot.rounded())
        // Only a bar the price scale covers (its centre on screen): the half-hidden bar past
        // an edge is outside `yRange`, and the crosshair dot would float off the canvas.
        let r = visibleRange
        return min(max(i, r.lowerBound), r.upperBound - 1)
    }
}

extension ChartSeries {
    /// The overlay line for an MA indicator (empty for anything else).
    func movingAverage(_ indicator: TechnicalIndicatorType) -> [Double?] {
        switch indicator {
        case .ma20:  return sma20
        case .ma50:  return sma50
        case .ma200: return sma200
        case .bollingerBands, .volume, .rsi14, .macd, .stochastic: return []
        }
    }
}
