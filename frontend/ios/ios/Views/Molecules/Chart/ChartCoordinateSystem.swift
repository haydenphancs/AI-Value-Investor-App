//
//  ChartCoordinateSystem.swift
//  ios
//
//  Shared coordinate math for chart rendering
//

import SwiftUI

struct ChartCoordinateSystem {
    let width: CGFloat
    let height: CGFloat
    let minValue: Double
    let maxValue: Double
    let dataCount: Int

    /// Normalized time fractions [0..1] for each data point within the trading session.
    /// When set (intraday 1D), xPosition uses time-based mapping instead of index-based.
    /// nil means use default linear index mapping.
    let timeFractions: [CGFloat]?

    private var valueRange: Double {
        max(maxValue - minValue, Double.ulpOfOne)
    }

    func xPosition(for index: Int) -> CGFloat {
        // Time-based mapping for intraday charts
        if let fracs = timeFractions, index >= 0, index < fracs.count {
            return fracs[index] * width
        }
        // Default: linear index mapping
        guard dataCount > 1 else { return width / 2 }
        return CGFloat(index) * width / CGFloat(dataCount - 1)
    }

    func yPosition(for value: Double) -> CGFloat {
        let normalized = (value - minValue) / valueRange
        return height - (CGFloat(normalized) * height * 0.9) - height * 0.05
    }

    /// Create from close prices (no time mapping)
    static func from(closes: [Double], size: CGSize) -> ChartCoordinateSystem {
        ChartCoordinateSystem(
            width: size.width,
            height: size.height,
            minValue: closes.min() ?? 0,
            maxValue: closes.max() ?? 1,
            dataCount: closes.count,
            timeFractions: nil
        )
    }

    /// Create from OHLCV data (uses full high/low range, no time mapping)
    static func from(pricePoints: [StockPricePoint], size: CGSize) -> ChartCoordinateSystem {
        let highs = pricePoints.compactMap { $0.high }
        let lows = pricePoints.compactMap { $0.low }
        let closes = pricePoints.map { $0.close }

        let maxVal = highs.isEmpty ? (closes.max() ?? 1) : max(highs.max() ?? 1, closes.max() ?? 1)
        let minVal = lows.isEmpty ? (closes.min() ?? 0) : min(lows.min() ?? 0, closes.min() ?? 0)

        return ChartCoordinateSystem(
            width: size.width,
            height: size.height,
            minValue: minVal,
            maxValue: maxVal,
            dataCount: pricePoints.count,
            timeFractions: nil
        )
    }

    // MARK: - Intraday Time-Based Factory

    /// Create a coordinate system where X positions are mapped by time within the trading day.
    /// The full width represents the trading session (e.g. 9:30 AM - 4:00 PM ET).
    /// Data that doesn't fill the full session leaves empty space on the right.
    static func intradayTimeBased(
        closes: [Double],
        pricePoints: [StockPricePoint],
        size: CGSize,
        useOHLC: Bool = false,
        window: TradingDayHelper.SessionWindow = .regular
    ) -> ChartCoordinateSystem {
        let fracs = TradingDayHelper.timeFractions(for: pricePoints, window: window)

        let minVal: Double
        let maxVal: Double
        if useOHLC {
            let highs = pricePoints.compactMap { $0.high }
            let lows = pricePoints.compactMap { $0.low }
            maxVal = highs.isEmpty ? (closes.max() ?? 1) : max(highs.max() ?? 1, closes.max() ?? 1)
            minVal = lows.isEmpty ? (closes.min() ?? 0) : min(lows.min() ?? 0, closes.min() ?? 0)
        } else {
            minVal = closes.min() ?? 0
            maxVal = closes.max() ?? 1
        }

        return ChartCoordinateSystem(
            width: size.width,
            height: size.height,
            minValue: minVal,
            maxValue: maxVal,
            dataCount: pricePoints.count,
            timeFractions: fracs
        )
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

    /// Generate evenly-spaced time labels across the trading session in the user's local timezone.
    /// Returns `count` labels like ["7:30 AM", "9:05 AM", "10:40 AM", "12:15 PM"].
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

        // Use today (or the reference date's day) as the base
        let baseDate = referenceDate ?? Date()
        var openComps = etCalendar.dateComponents([.year, .month, .day], from: baseDate)
        openComps.hour = window.openMinute / 60
        openComps.minute = window.openMinute % 60
        openComps.second = 0

        guard let openDate = etCalendar.date(from: openComps) else { return [] }

        // Local timezone formatter for display
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        // Uses device's local timezone by default — this is what we want

        var labels: [String] = []
        for i in 0..<count {
            let minuteOffset = Double(i) * Double(window.length) / Double(count - 1)
            guard let labelDate = Calendar.current.date(
                byAdding: .minute,
                value: Int(minuteOffset),
                to: openDate
            ) else { continue }
            labels.append(formatter.string(from: labelDate))
        }
        return labels
    }
}
