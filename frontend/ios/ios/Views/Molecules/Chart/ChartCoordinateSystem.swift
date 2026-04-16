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
        useOHLC: Bool = false
    ) -> ChartCoordinateSystem {
        let fracs = TradingDayHelper.timeFractions(for: pricePoints)

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

/// Maps intraday data points to their fractional position within the regular trading session.
enum TradingDayHelper {

    // Regular session: 9:30 AM - 4:00 PM ET  (570 - 960 minutes from midnight)
    static let marketOpenMinute = 9 * 60 + 30   // 570
    static let marketCloseMinute = 16 * 60        // 960
    static let sessionLength = marketCloseMinute - marketOpenMinute  // 390 minutes

    /// Compute normalized [0..1] time fractions for each price point.
    /// 0.0 = market open (9:30 AM ET), 1.0 = market close (4:00 PM ET).
    /// Pre-market points clamp to 0, after-hours clamp to 1.
    static func timeFractions(for pricePoints: [StockPricePoint]) -> [CGFloat] {
        let etTimeZone = TimeZone(identifier: "America/New_York")!
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = etTimeZone

        return pricePoints.map { point in
            guard let date = ChartDateFormatters.parseDate(point.date) else {
                return 0
            }
            let comps = calendar.dateComponents([.hour, .minute], from: date)
            guard let hour = comps.hour, let minute = comps.minute else { return 0 }
            let minuteOfDay = hour * 60 + minute

            let fraction = CGFloat(minuteOfDay - marketOpenMinute) / CGFloat(sessionLength)
            return max(0, min(1, fraction))
        }
    }

    /// Generate evenly-spaced time labels across the trading session in the user's local timezone.
    /// Returns `count` labels like ["7:30 AM", "9:05 AM", "10:40 AM", "12:15 PM"].
    static func sessionTimeLabels(count: Int, referenceDate: Date? = nil) -> [String] {
        guard count > 1 else { return [] }

        let etTimeZone = TimeZone(identifier: "America/New_York")!
        var etCalendar = Calendar(identifier: .gregorian)
        etCalendar.timeZone = etTimeZone

        // Use today (or the reference date's day) as the base
        let baseDate = referenceDate ?? Date()
        var openComps = etCalendar.dateComponents([.year, .month, .day], from: baseDate)
        openComps.hour = 9
        openComps.minute = 30
        openComps.second = 0

        guard let openDate = etCalendar.date(from: openComps) else { return [] }

        // Local timezone formatter for display
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        // Uses device's local timezone by default — this is what we want

        var labels: [String] = []
        for i in 0..<count {
            let minuteOffset = Double(i) * Double(sessionLength) / Double(count - 1)
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
