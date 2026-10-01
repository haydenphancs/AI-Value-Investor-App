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

    /// The timeline's entry dates: SEVERAL renders of one snapshot before `reload`.
    ///
    /// Re-rendering buys no new DATA between fetches; what it buys is an honest LABEL.
    /// `WidgetSessionLabel` derives the wording at render time, so a tile written at 14:14
    /// says "Live 2:14 PM ET" now and "As of 2:14 PM ET" an hour later with no network.
    ///
    /// ⚠️ THE DAY ROLLOVER IS 00:01 **ET**, NOT DEVICE-LOCAL. The aged label compares ET days,
    /// so only an entry past ET midnight can turn today's numbers into "Tue close". This used
    /// `Calendar.current`: for a user in UTC+7 the local 00:01 landed AFTER the 04:00 ET
    /// reload and was dropped, leaving 00:00-04:00 ET (11:00-15:00 local) with Tuesday's move
    /// presented as today's; Europe went unlabelled for up to 18 hours.
    ///
    /// Pure and clock-injected so `scripts/widget-refresh-schedule-check.sh` can assert it
    /// under several device time zones.
    ///
    /// - Returns: sorted, de-duplicated, starting at `now`, every date before `reload` (a
    ///   render past the reload is redundant — the reload replaces it).
    ///
    /// During regular hours that is `[now]` alone. The one render the snapshot itself calls for
    /// — the instant its "As of" label starts speaking — is added by the provider once the
    /// snapshot is known (`WidgetSessionLabel.ageBoundary`), and deliberately NOT capped at the
    /// reload: it is the render a deferred reload needs.
    public static func renderDates(now: Date, reload: Date) -> [Date] {
        var dates: [Date] = [now]
        for minutes in [20, 60, 180] {
            let d = now.addingTimeInterval(TimeInterval(minutes * 60))
            // On a quiet weekend these are the only thing keeping the label moving before
            // the reload; past it they would be dead weight.
            if d < reload { dates.append(d) }
        }
        if let rollover = easternCalendar.nextDate(
            after: now, matching: DateComponents(hour: 0, minute: 1),
            matchingPolicy: .nextTime
        ), rollover < reload {
            dates.append(rollover)
        }
        return Array(Set(dates)).sorted()
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
