//
//  CreditRenewalDate.swift
//  ios
//
//  The day a monthly credit allowance renews, as shown on Account ("Renews on Nov 25") and on
//  the Research credits card ("Renews Nov 25, 2026").
//
//  `resets_at` (GET /users/me/credits) holds one of two kinds of instant since migration 196:
//
//  * CALENDAR — Free, complimentary and Sandbox/TestFlight allowances renew at 00:00 ET on the
//    1st (`ensure_credit_period`). Rendered in ET: in the device's zone, everyone west of New
//    York saw the day BEFORE their credits come back ("Oct 31" for a Nov 1 reset).
//  * PAID PERIOD — a Pro/Max subscriber's allowance renews with Apple's billing period, at
//    Apple's `expiresDate`. Rendered in the DEVICE's zone, which is how Apple's own subscription
//    screen and receipt state the renewal day; pinned to ET it read one day early for anyone east
//    of New York whose renewal falls in the evening ET.
//
//  The kind is read off the instant itself: a calendar boundary is exactly midnight ET on the
//  1st, and Apple's expiresDate (millisecond precision, the purchase time of day) essentially
//  never is. If one ever lands exactly there, it is shown in ET — the same date for anyone in
//  New York's day. No backend field is needed, so an older backend renders correctly too.
//
//  Both formatters pin `en_US_POSIX` + Gregorian, like every Account-screen date, so a phone set
//  to a Buddhist, Japanese or Islamic calendar still reads the same month and day as the rest of
//  the app.
//

import Foundation

enum CreditRenewalDate {
    private static let eastern = TimeZone(identifier: "America/New_York")

    /// True for the calendar allowance's boundary: 00:00:00 ET on the 1st of a month.
    static func isCalendarBoundary(_ date: Date) -> Bool {
        guard let eastern else { return false }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = eastern
        let c = calendar.dateComponents([.day, .hour, .minute, .second], from: date)
        return c.day == 1 && c.hour == 0 && c.minute == 0 && c.second == 0
    }

    /// `"Nov 25"` — Account's "Renews on …".
    static func short(_ date: Date) -> String {
        (isCalendarBoundary(date) ? shortEastern : shortLocal).string(from: date)
    }

    /// `"Nov 25, 2026"` — the Research credits card.
    static func long(_ date: Date) -> String {
        (isCalendarBoundary(date) ? longEastern : longLocal).string(from: date)
    }

    private static let shortEastern = formatter("MMM d", timeZone: eastern)
    private static let shortLocal = formatter("MMM d", timeZone: .autoupdatingCurrent)
    private static let longEastern = formatter("MMM d, yyyy", timeZone: eastern)
    private static let longLocal = formatter("MMM d, yyyy", timeZone: .autoupdatingCurrent)

    private static func formatter(_ pattern: String, timeZone: TimeZone?) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = timeZone
        f.dateFormat = pattern
        return f
    }
}
