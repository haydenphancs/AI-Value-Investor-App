//
//  WeeklyQuotePicker.swift
//  ios
//
//  Which of the 52 bundled investor quotes the brand cover (`CaydexSloganView`) shows this
//  week, as pure functions over plain values.
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`. There is no XCTest target in this project, so
//  the only way to EXECUTE this date logic in CI is to pipe this file into `xcrun swift -`
//  from pytest (`backend/tests/test_ios_weekly_investor_quotes.py`, same mechanism as
//  `LessonNarrationPolicy`). A SwiftUI import makes it unrunnable there.
//
//  THE RULE (TestFlight 1.0(6), developer request 2026-09-23): "We need 52 quotes (1 year)
//  it will repeat weekly for every year." Entry N shows during ISO-8601 week N, so a given
//  week shows the same quote every year.
//
//  Three decisions this file encodes, each with a test:
//   1. ISO WEEK 53 REPEATS ENTRY 52 (clamp), it does not wrap to entry 1. 52 quotes and 53
//      weeks means one quote runs twice whichever way; week 53 belongs to the OLD ISO year
//      (it holds that year's last Thursday), so it carries on that year's final entry, and
//      week 1 of the new year still changes the quote on its Monday. The brief suggested
//      `(week - 1) % 52`, which would show entry 1 in week 53 and then AGAIN in week 1.
//      The next week 53 is 2026-12-28 → 2027-01-03.
//   2. DEVICE-LOCAL time zone (`autoupdatingCurrent`), not ET. Nothing server-side shares
//      this choice (unlike `ChatStartersStore`, whose server composes by the ET trading day),
//      so a person's week starts at their own Monday 00:00 — ET would roll the quote over at
//      2 pm on Monday in Tokyo. `autoupdatingCurrent` follows a traveller's zone change.
//   3. The calendar's zone is ALWAYS assigned. `Calendar(identifier:)` takes its zone from
//      `NSTimeZone.default`, which can differ from `TimeZone.current` (measured on this Mac),
//      and `Calendar.current` carries the user's locale-driven week rules (US weeks start on
//      Sunday and week 1 contains Jan 1) — neither is ISO. Never `hashValue` either: Swift
//      seeds it per launch.
//

import Foundation

enum WeeklyQuotePicker {

    /// One authored quote per ISO week 1…52.
    static let weeksPerCycle = 52

    /// The ISO-8601 calendar in `timeZone`. The week rules are set explicitly even though
    /// `.iso8601` implies them, so the contract is visible where it is relied on.
    static func isoCalendar(timeZone: TimeZone = .autoupdatingCurrent) -> Calendar {
        var calendar = Calendar(identifier: .iso8601)
        calendar.firstWeekday = 2            // Monday
        calendar.minimumDaysInFirstWeek = 4  // the week holding the year's first Thursday
        calendar.timeZone = timeZone
        return calendar
    }

    /// 0-based slot for an ISO week. 1…52 map straight through; 53 clamps to 51 (repeats
    /// entry 52). Out-of-range input is clamped, never trapped.
    static func slot(forISOWeek week: Int) -> Int {
        min(max(week, 1), weeksPerCycle) - 1
    }

    /// The index to show on `date`, or nil when there is nothing to show (`count <= 0`).
    /// A count other than 52 is a content bug (asserted by the loader in DEBUG); here it
    /// degrades to `slot % count`, which can never index out of bounds.
    static func index(for date: Date, count: Int,
                      timeZone: TimeZone = .autoupdatingCurrent) -> Int? {
        guard count > 0 else { return nil }
        let week = isoCalendar(timeZone: timeZone).component(.weekOfYear, from: date)
        return slot(forISOWeek: week) % count
    }

    /// The item for `date`'s ISO week, or nil for an empty list.
    static func pick<T>(_ items: [T], on date: Date = Date(),
                        timeZone: TimeZone = .autoupdatingCurrent) -> T? {
        index(for: date, count: items.count, timeZone: timeZone).map { items[$0] }
    }
}
