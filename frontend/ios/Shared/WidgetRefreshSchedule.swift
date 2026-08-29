//
//  WidgetRefreshSchedule.swift
//  Caydex
//
//  When the widget should next ask WidgetKit to wake it.
//

import Foundation

/// Decides the timeline's reload date, in ET market terms.
///
/// ⚠️ THE BUDGET IS THE CONSTRAINT, NOT THE INTERVAL.
///
/// WidgetKit grants a widget only a few dozen timeline refreshes a day and adapts the
/// allowance to how often the tile is actually looked at. A flat 20-minute cadence asks
/// for ~72 and gets throttled — which can leave the tile staler than a modest cadence
/// would have. So the spend is concentrated where prices actually move:
///
///     regular session (09:30-16:00 ET)   +20 min   ~20 requests
///     pre-market / after-hours           +60 min   ~10 requests
///     overnight, weekend                 next 04:00 ET pre-market open
///
/// ≈30 on a trading day, comfortably inside the allowance.
///
/// ⚠️ NO CLIENT-SIDE HOLIDAY TABLE, deliberately. The backend owns
/// `market_hours.US_MARKET_HOLIDAYS`, and a second copy shipped in an app binary would
/// drift the moment a year rolls over — silently, since nothing renders it. Being wrong
/// on a holiday costs a handful of refreshes that return an unchanged payload; being
/// wrong about a DATE would cost a wrong number, which is why the session LABEL is
/// derived from the server's `session_date` instead of from this file.
public enum WidgetRefreshSchedule {
    // Minute-of-day boundaries, ET. Mirrors `market_hours.py`.
    static let premarketStart = 4 * 60          // 04:00
    static let regularOpen = 9 * 60 + 30        // 09:30
    static let regularClose = 16 * 60           // 16:00
    static let afterHoursEnd = 20 * 60          // 20:00

    static let regularInterval: TimeInterval = 20 * 60
    static let extendedInterval: TimeInterval = 60 * 60

    static var easternCalendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        return cal
    }

    /// When WidgetKit should be asked for the next timeline.
    ///
    /// Always strictly after `now` — a date in the past makes WidgetKit reload
    /// immediately and burn the allowance in a loop.
    public static func nextRefresh(after now: Date) -> Date {
        let cal = easternCalendar
        let comps = cal.dateComponents([.hour, .minute, .weekday], from: now)
        let minuteOfDay = (comps.hour ?? 0) * 60 + (comps.minute ?? 0)
        // Calendar.weekday: 1 = Sunday, 7 = Saturday.
        let weekday = comps.weekday ?? 1
        let isWeekend = (weekday == 1 || weekday == 7)

        if isWeekend {
            return nextPremarketOpen(after: now, cal: cal)
        }
        if minuteOfDay < premarketStart {
            return atMinute(premarketStart, on: now, cal: cal) ?? now.addingTimeInterval(extendedInterval)
        }
        if minuteOfDay >= afterHoursEnd {
            return nextPremarketOpen(after: now, cal: cal)
        }

        let interval = (minuteOfDay >= regularOpen && minuteOfDay < regularClose)
            ? regularInterval
            : extendedInterval
        let candidate = now.addingTimeInterval(interval)

        // Do not sleep THROUGH the opening bell: an hour-long pre-market step taken at
        // 09:00 would otherwise land at 10:00 and skip the first half hour of the
        // session, which is the busiest part of the day.
        if minuteOfDay < regularOpen,
           let open = atMinute(regularOpen, on: now, cal: cal),
           candidate > open {
            return open
        }
        return candidate
    }

    /// 04:00 ET on the next weekday. Not "tomorrow" — on a Friday evening that is Monday.
    static func nextPremarketOpen(after now: Date, cal: Calendar) -> Date {
        var probe = now
        // Bounded rather than `while true`: a malformed calendar must return a slightly
        // wrong date, never hang a timeline callback.
        for _ in 0..<8 {
            guard let next = cal.date(byAdding: .day, value: 1, to: probe) else { break }
            probe = next
            let weekday = cal.component(.weekday, from: probe)
            if weekday == 1 || weekday == 7 { continue }
            if let open = atMinute(premarketStart, on: probe, cal: cal), open > now {
                return open
            }
        }
        return now.addingTimeInterval(extendedInterval)
    }

    static func atMinute(_ minuteOfDay: Int, on day: Date, cal: Calendar) -> Date? {
        var c = cal.dateComponents([.year, .month, .day], from: day)
        c.hour = minuteOfDay / 60
        c.minute = minuteOfDay % 60
        c.second = 0
        return cal.date(from: c)
    }
}
