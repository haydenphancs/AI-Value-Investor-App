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
/// WidgetKit grants a widget only a few dozen timeline refreshes a day (Apple: typically 40
/// to 70 for a widget the user looks at often, per widget) and adapts the allowance to how
/// often the tile is actually looked at. A flat 20-minute cadence asks for ~72 and gets
/// throttled — which can leave the tile staler than a modest cadence would have. So the spend
/// is concentrated where prices actually move:
///
///     regular session (09:30-16:00 ET)              +20 min   20 requests
///     pre-market / after-hours                      +60 min   10 requests
///     overnight, weekend, closed for the day        +90 min   the rest, never sleeping
///                                                             past a weekday's 04:00 ET
///
/// 35 requests on a trading day, 15 on a weekend day, 16 on a full holiday and 27 on a 13:00
/// half day — under 40 on every day, pinned by `scripts/widget-refresh-schedule-check.sh`.
/// A reload the APP asks for (foreground, a published credential) and the in-tile ⇆ toggle
/// are not charged to this allowance.
///
/// WHY THE QUIET HOURS NO LONGER SLEEP (owner, 2026-11-27: "Every 90 min (Recommended)").
/// The tile used to sleep from 20:00 ET to the next weekday's 04:00, and all weekend. That was
/// harmless while only the Market tile fetched. Once the extension fetches Holdings itself, a
/// sleeping tile shows Friday evening's portfolio until Monday, keeps a remotely signed-out
/// account's holdings on screen until the next weekday (sign out of all devices is the lost-
/// phone lever), and never moves the round-the-clock Bitcoin row. 90 minutes, not 60: an hour
/// would ask for 37 on a trading day and 23 on a weekend day. A deferred quiet reload is not
/// silent either: the footer turns into "As of …" past `WidgetSessionLabel.offHoursAgeLimit`.
///
/// ⚠️ NO CLIENT-SIDE HOLIDAY TABLE, deliberately. The backend owns
/// `market_hours.US_MARKET_HOLIDAYS`, and a second copy shipped in an app binary would
/// drift the moment a year rolls over — silently, since nothing renders it. Being wrong
/// on a holiday costs a handful of refreshes that return an unchanged payload; being
/// wrong about a DATE would cost a wrong number, which is why the session LABEL is
/// derived from the server's `session_date` instead of from this file. What ends a holiday
/// or a half day early is the SERVER's own phase, already on every payload: see
/// `closedForTheDay`.
public enum WidgetRefreshSchedule {
    // Minute-of-day boundaries, ET. Mirrors `market_hours.py`.
    static let premarketStart = 4 * 60          // 04:00
    static let regularOpen = 9 * 60 + 30        // 09:30
    static let regularClose = 16 * 60           // 16:00
    static let afterHoursEnd = 20 * 60          // 20:00

    static let regularInterval: TimeInterval = 20 * 60
    static let extendedInterval: TimeInterval = 60 * 60
    /// Overnight, weekends, and the rest of a day the server has called closed. Also the
    /// longest step this schedule ever takes, which bounds how long a revoked Holdings grant
    /// can stay on screen (plus WidgetKit's own deferral).
    static let quietInterval: TimeInterval = 90 * 60
    /// How far AFTER `now` a payload's `as_of` may be and still count as "built now".
    ///
    /// The provider reads `now` BEFORE it fetches, and a fetch may take up to
    /// `WidgetAPIConfig.requestTimeout` (12 s); the server's clock may also run a little ahead
    /// of the phone's. A strict `builtAt <= now` would therefore ignore the hint on almost
    /// every fresh build. Five minutes is generous for that and still far short of letting a
    /// build from a skewed clock decide a day it was not built on (the same-ET-day test).
    static let buildSkewTolerance: TimeInterval = 5 * 60

    static var easternCalendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        return cal
    }

    /// When WidgetKit should be asked for the next timeline.
    ///
    /// Always strictly after `now`, and never more than `quietInterval` after it — a date in
    /// the past makes WidgetKit reload immediately and burn the allowance in a loop.
    ///
    /// - Parameters:
    ///   - marketSession: the snapshot's `market_session`, exactly as the server sent it, or
    ///     nil when there is no snapshot. Only an exact `"closed"` changes anything.
    ///   - builtAt: the same snapshot's `as_of`. The phase is only true AT that instant, so
    ///     the hint is ignored unless it was built on now's ET day (`closedForTheDay`).
    public static func nextRefresh(
        after now: Date, marketSession: String? = nil, builtAt: Date? = nil
    ) -> Date {
        let cal = easternCalendar
        let comps = cal.dateComponents([.hour, .minute, .weekday], from: now)
        let minuteOfDay = (comps.hour ?? 0) * 60 + (comps.minute ?? 0)
        // Calendar.weekday: 1 = Sunday, 7 = Saturday.
        let weekday = comps.weekday ?? 1
        let isWeekend = (weekday == 1 || weekday == 7)

        // ONE quiet branch for every hour nothing trades: the weekend, overnight on either side
        // of the extended session, and the rest of a holiday or a half day.
        if isWeekend || minuteOfDay < premarketStart || minuteOfDay >= afterHoursEnd
            || closedForTheDay(marketSession: marketSession, builtAt: builtAt, now: now, cal: cal) {
            let step = now.addingTimeInterval(quietInterval)
            // Do not sleep THROUGH a weekday's pre-market open: a 90-minute step taken at
            // 03:00 would otherwise land at 04:30 and skip the first half hour of pre-market.
            if !isWeekend, minuteOfDay < premarketStart,
               let open = atMinute(premarketStart, on: now, cal: cal), open < step {
                return open
            }
            return step
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

    /// Whether the server has already called the rest of now's ET day closed — a holiday, or a
    /// half day after its 13:00 close.
    ///
    /// The backend's `session_phase` never reopens a weekday once it reads "closed" at or after
    /// 04:00 ET (the holiday, half-day and 20:00 branches all hold to ET midnight), so a build
    /// in that window that says "closed" ends the day's market cadence. True only when ALL of:
    ///   • `marketSession` is exactly "closed" (the wire's closed set, compared exactly — an
    ///     unknown or re-cased phase changes nothing);
    ///   • `builtAt` is no later than `now + buildSkewTolerance` (see that constant);
    ///   • `builtAt` is on now's ET day, and that day is a weekday — yesterday's closed build,
    ///     or a Saturday one, says nothing about today;
    ///   • `builtAt` is between 04:00 and 20:00 ET — a 03:59 build is "closed" every weekday.
    ///
    /// The worst a wrong hint can do is put the rest of ONE ET day on the 90-minute cadence;
    /// it can never freeze a trading day, because no branch here sleeps longer than that.
    static func closedForTheDay(
        marketSession: String?, builtAt: Date?, now: Date, cal: Calendar
    ) -> Bool {
        guard marketSession == "closed", let builtAt else { return false }
        guard builtAt <= now.addingTimeInterval(buildSkewTolerance) else { return false }
        guard cal.isDate(builtAt, inSameDayAs: now) else { return false }
        let parts = cal.dateComponents([.hour, .minute, .weekday], from: builtAt)
        let weekday = parts.weekday ?? 1
        guard weekday != 1, weekday != 7 else { return false }
        let builtMinute = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        return builtMinute >= premarketStart && builtMinute < afterHoursEnd
    }

    /// The timeline's entry dates: SEVERAL renders of one snapshot.
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
    /// ⚠️ THE ROLLOVER IS ALWAYS AN ENTRY, even past the reload. It used to be kept only when
    /// it came before the reload, which was every post-20:00 timeline while the night slept to
    /// 04:00. With a 90-minute quiet reload no timeline built before 22:31 ET reaches midnight,
    /// so one deferred reload left yesterday's evening render up past 00:00: no footer, a cause
    /// still saying "today", and a tap opening yesterday's card as live. An entry past the
    /// reload costs nothing — a reload that runs replaces the timeline — and is exactly what a
    /// deferred one needs.
    ///
    /// Pure and clock-injected so `scripts/widget-refresh-schedule-check.sh` can assert it
    /// under several device time zones.
    ///
    /// - Returns: sorted, de-duplicated, starting at `now`: the +20/+60/+180-minute renders
    ///   that come before `reload` (past it they are dead weight), then the next ET 00:01.
    ///
    /// During regular hours that is `[now, next 00:01]`. The one render the snapshot itself
    /// calls for — the instant its "As of" label starts speaking — is added by the provider
    /// once the snapshot is known (`WidgetSessionLabel.ageBoundary`), and is likewise NOT
    /// capped at the reload.
    public static func renderDates(now: Date, reload: Date) -> [Date] {
        var dates: [Date] = [now]
        for minutes in [20, 60, 180] {
            let d = now.addingTimeInterval(TimeInterval(minutes * 60))
            // On a quiet evening these are the only thing keeping the label moving before
            // the reload; past it they would be dead weight.
            if d < reload { dates.append(d) }
        }
        if let rollover = easternCalendar.nextDate(
            after: now, matching: DateComponents(hour: 0, minute: 1),
            matchingPolicy: .nextTime
        ) {
            dates.append(rollover)
        }
        return Array(Set(dates)).sorted()
    }

    static func atMinute(_ minuteOfDay: Int, on day: Date, cal: Calendar) -> Date? {
        var c = cal.dateComponents([.year, .month, .day], from: day)
        c.hour = minuteOfDay / 60
        c.minute = minuteOfDay % 60
        c.second = 0
        return cal.date(from: c)
    }
}
